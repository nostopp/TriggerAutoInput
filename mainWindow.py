import builtins
import json
import os
import queue
import threading
import sys
import ctypes
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import psutil
from pynput import keyboard, mouse

from auto_input_manager import AutoInputManager


RECORD_OUTPUT_PATH = os.path.join("config", "recorded.json")
CLICK_MERGE_THRESHOLD_SECONDS = 0.1


def get_app_root() -> str:
    """Return the directory that should be treated as the app root.

    When packaged by PyInstaller, resources should be resolved relative to the
    executable. During source execution, resolve them relative to this file.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class PrintForwarder:
    """Temporarily replace builtin print so each line is queued for the GUI and also written to the console."""

    def __init__(self, log_queue: queue.Queue):
        self.log_queue = log_queue
        self.original_print = builtins.print

    def __enter__(self):
        builtins.print = self._print
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        builtins.print = self.original_print

    def _print(self, *args, **kwargs):
        file_target = kwargs.get("file")
        text = kwargs.get("sep", " ").join(str(arg) for arg in args)
        text += kwargs.get("end", "\n")
        target_streams = (None, sys.stdout, sys.stderr)
        if file_target in target_streams:
            try:
                self.log_queue.put(text)
            except Exception:
                pass
        # return self.original_print(*args, **kwargs)
        return None


class InputRecorder:
    def __init__(self, root: tk.Tk, log_callback, status_callback, finish_callback, output_path: str):
        self.root = root
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.output_path = output_path
        self.keyboard_listener: keyboard.Listener | None = None
        self.mouse_listener: mouse.Listener | None = None
        self.recording = False
        self.awaiting_trigger = False
        self.trigger_key: str | None = None
        self._pending_trigger: tuple[str, str] | None = None
        self._pending_actions: dict[tuple[str, str], tuple[dict, float]] = {}
        self._events: list[dict] = []
        self._last_action_time: float | None = None
        self._ctrl_pressed = False
        self._shift_pressed = False

    def start(self) -> bool:
        if self.recording:
            return False

        self.recording = True
        self.awaiting_trigger = True
        self.trigger_key = None
        self._pending_trigger = None
        self._pending_actions = {}
        self._events = []
        self._last_action_time = None
        self._ctrl_pressed = False
        self._shift_pressed = False

        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_keyboard_press,
            on_release=self._on_keyboard_release,
        )
        self.mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self.keyboard_listener.start()
        self.mouse_listener.start()

        self._set_status("等待触发键...")
        self._log("开始录制：请按一个键盘单键或鼠标键作为触发键")
        return True

    def stop(self) -> dict | None:
        if not self.recording:
            return None

        self.recording = False
        self.awaiting_trigger = False
        self._stop_listeners()

        if not self.trigger_key:
            self._set_status("录制已取消")
            self._log("录制结束，但未捕获到触发键")
            return None

        if self._pending_actions:
            self._log(f"录制结束时仍有未闭合按键，已忽略 {len(self._pending_actions)} 个未闭合事件")
            ignored_events = {id(event_info[0]) for event_info in self._pending_actions.values()}
            self._events = [event for event in self._events if id(event) not in ignored_events]
            self._pending_actions = {}

        actions = self._build_actions()
        payload = {
            self.trigger_key: {
                "trigger_type": "once",
                "actions": actions,
            }
        }
        return payload

    def _stop_listeners(self):
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None

    def _set_status(self, text: str):
        self.root.after(0, lambda: self.status_callback(text))

    def _log(self, text: str):
        self.root.after(0, lambda: self.log_callback(text))

    def _normalize_keyboard_key(self, key) -> str | None:
        char = getattr(key, "char", None)
        if char:
            return char.lower()
        return None

    def _event_time(self) -> float:
        return time.perf_counter()

    def _append_delay_before(self, event_time: float):
        if self._last_action_time is None:
            self._last_action_time = event_time
            return
        delay = event_time - self._last_action_time
        if delay > 0:
            self._events.append({"kind": "delay", "duration": delay})
        self._last_action_time = event_time

    def _record_action(self, event: dict, event_time: float):
        self._append_delay_before(event_time)
        self._events.append(event)

    def _capture_trigger(self, trigger_key: str):
        self.trigger_key = trigger_key
        self.awaiting_trigger = False
        self._pending_trigger = None
        self._pending_actions = {}
        self._events = []
        self._last_action_time = None
        self._set_status("正在录制动作...")
        self._log(f"触发键已确认：{trigger_key}")
        self._log("开始录制动作，按 Ctrl+Shift+C 结束")

    def _on_keyboard_press(self, key):
        normalized = self._normalize_keyboard_key(key)

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = True
            return
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._shift_pressed = True
            return
        if normalized in ("c", "\x03"):
            if self._ctrl_pressed and self._shift_pressed:
                self.root.after(0, self.finish_callback)
                return

        if not self.recording:
            return

        if self.awaiting_trigger:
            if normalized:
                self._pending_trigger = ("keyboard", normalized)
            return

        if not normalized:
            return

        event_time = self._event_time()
        pending_key = ("keyboard", normalized)
        if pending_key not in self._pending_actions:
            event = {"kind": "event", "time": event_time, "type": "keyboard", "action": "press", "key": normalized}
            self._pending_actions[pending_key] = (event, event_time)
            self._record_action(event, event_time)

    def _on_keyboard_release(self, key):
        normalized = self._normalize_keyboard_key(key)

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = False
            return
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._shift_pressed = False
            return

        if not self.recording:
            return

        if self.awaiting_trigger:
            if self._pending_trigger == ("keyboard", normalized):
                self._capture_trigger(f"keyboard_{normalized}")
            return

        if not normalized:
            return

        event_time = self._event_time()
        self._pending_actions.pop(("keyboard", normalized), None)
        event = {"kind": "event", "time": event_time, "type": "keyboard", "action": "release", "key": normalized}
        self._record_action(event, event_time)

    def _on_mouse_click(self, x, y, button, pressed):
        if not self.recording:
            return

        button_name = getattr(button, "name", None)
        if not button_name:
            return

        if self.awaiting_trigger:
            if pressed:
                self._pending_trigger = ("mouse", button_name)
            elif self._pending_trigger == ("mouse", button_name):
                self._capture_trigger(f"mouse_{button_name}")
            return

        event_time = self._event_time()
        action = "press" if pressed else "release"
        pending_key = ("mouse", button_name)
        if pressed:
            if pending_key in self._pending_actions:
                return
            event = {"kind": "event", "time": event_time, "type": "mouse", "action": action, "key": button_name}
            self._pending_actions[pending_key] = (event, event_time)
        else:
            self._pending_actions.pop(pending_key, None)
            event = {"kind": "event", "time": event_time, "type": "mouse", "action": action, "key": button_name}
        self._record_action(event, event_time)

    def _build_actions(self) -> list[dict]:
        result: list[dict] = []
        idx = 0
        while idx < len(self._events):
            event = self._events[idx]
            if event["kind"] == "delay":
                duration = round(event["duration"], 3)
                if duration > 0:
                    result.append({"type": "delay", "duration": duration})
                idx += 1
                continue

            next_event = None
            next_idx = -1
            interstitial_delay = None
            if idx + 2 < len(self._events) and self._events[idx + 1]["kind"] == "delay" and self._events[idx + 2]["kind"] == "event":
                interstitial_delay = self._events[idx + 1]
                next_event = self._events[idx + 2]
                next_idx = idx + 2
            elif idx + 1 < len(self._events) and self._events[idx + 1]["kind"] == "event":
                next_event = self._events[idx + 1]
                next_idx = idx + 1

            if (
                next_event
                and event["type"] == next_event["type"]
                and event["key"] == next_event["key"]
                and event["action"] == "press"
                and next_event["action"] == "release"
            ):
                gap_seconds = interstitial_delay["duration"] if interstitial_delay else (next_event["time"] - event["time"])
                if gap_seconds < CLICK_MERGE_THRESHOLD_SECONDS:
                    result.append({"type": event["type"], "action": "click", "key": event["key"]})
                    idx = next_idx + 1
                    continue

            result.append({"type": event["type"], "action": event["action"], "key": event["key"]})
            idx += 1

        return result


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TriggerAutoInput GUI")
        self.project_root = get_app_root()
        self.log_queue: queue.Queue = queue.Queue()
        self.manager: AutoInputManager | None = None
        self.worker_thread: threading.Thread | None = None
        self.process_dump_thread: threading.Thread | None = None
        self.want_close = False
        self.action_text = tk.StringVar(value="启动")
        self.status_var = tk.StringVar(value="空闲")
        self.config_var = tk.StringVar(value=self._get_default_config_value())
        self.log_var = tk.BooleanVar(value=False)
        self.process_var = tk.StringVar(value="")
        self.recorder = InputRecorder(
            root=self.root,
            log_callback=self._queue_log,
            status_callback=self.status_var.set,
            finish_callback=self._finish_recording,
            output_path=os.path.join(self.project_root, RECORD_OUTPUT_PATH),
        )

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_logs()

    def _build_ui(self):
        padding_frame = ttk.Frame(self.root, padding=12)
        padding_frame.pack(fill=tk.BOTH, expand=True)

        param_frame = ttk.LabelFrame(padding_frame, text="运行参数", padding=10)
        param_frame.pack(fill=tk.X)
        param_frame.columnconfigure(1, weight=1)

        ttk.Label(param_frame, text="配置文件:").grid(row=0, column=0, sticky=tk.W)
        self.config_entry = ttk.Entry(param_frame, textvariable=self.config_var, width=40)
        self.config_entry.grid(row=0, column=1, sticky=tk.EW, padx=(4, 0))
        self.browse_button = ttk.Button(param_frame, text="浏览", command=self._on_browse)
        self.browse_button.grid(row=0, column=2, sticky=tk.W, padx=(4, 0))

        ttk.Label(param_frame, text="启用日志:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Checkbutton(param_frame, variable=self.log_var).grid(row=1, column=1, sticky=tk.W, pady=(8, 0))

        ttk.Label(param_frame, text="进程名 (可选):").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        self.process_entry = ttk.Entry(param_frame, textvariable=self.process_var, width=40)
        self.process_entry.grid(row=2, column=1, sticky=tk.EW, padx=(4, 0), pady=(8, 0))
        self.print_process_button = ttk.Button(param_frame, text="打印进程", command=self._on_print_processes)
        self.print_process_button.grid(row=2, column=2, sticky=tk.W, padx=(4, 0), pady=(8, 0))

        record_frame = ttk.Frame(padding_frame, padding=(0, 10, 0, 0))
        record_frame.pack(fill=tk.X)
        self.record_button = ttk.Button(record_frame, text="开始录制", command=self._toggle_recording)
        self.record_button.pack(side=tk.LEFT)
        ttk.Label(
            record_frame,
            text=f"录制输出固定覆盖到 {RECORD_OUTPUT_PATH}，结束快捷键 Ctrl+Shift+C",
        ).pack(side=tk.LEFT, padx=(12, 0))

        button_frame = ttk.Frame(padding_frame, padding=(0, 10, 0, 0))
        button_frame.pack(fill=tk.X)
        self.action_button = ttk.Button(button_frame, textvariable=self.action_text, command=self._on_action)
        self.action_button.pack(side=tk.LEFT)
        ttk.Label(button_frame, textvariable=self.status_var).pack(side=tk.LEFT, padx=(12, 0))

        log_frame = ttk.LabelFrame(padding_frame, text="运行日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = ScrolledText(log_frame, wrap=tk.WORD, height=15, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        info_frame = ttk.LabelFrame(padding_frame, text="说明", padding=6)
        info_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(info_frame, text="运行后请使用 Ctrl+Shift+X 暂停/恢复监听").pack(anchor=tk.W)

    def _get_default_config_value(self) -> str:
        default_config = os.path.join(self.project_root, "config", "example.json")
        if os.path.isfile(default_config):
            return self._to_display_path(default_config)
        return ""

    def _to_display_path(self, path: str) -> str:
        try:
            if os.path.commonpath([self.project_root, path]) == self.project_root:
                return os.path.relpath(path, self.project_root)
        except ValueError:
            pass
        return path

    def _on_browse(self):
        selected = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=os.path.join(self.project_root, "config"),
            filetypes=[
                ("JSON 文件", "*.json"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        self.config_var.set(self._to_display_path(selected))

    def _on_action(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._stop_manager()
        else:
            self._start_manager()

    def _queue_log(self, message: str):
        if not message.endswith("\n"):
            message += "\n"
        self.log_queue.put(message)

    def _toggle_recording(self):
        if self.recorder.recording:
            self._finish_recording()
            return

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("无法录制", "请先停止当前运行中的监听，再开始录制。")
            return

        started = self.recorder.start()
        if not started:
            return
        self.record_button.configure(text="停止录制")

    def _finish_recording(self):
        payload = self.recorder.stop()
        self.record_button.configure(text="开始录制")
        if payload is None:
            return

        process_name = self.process_var.get()
        if process_name:
            payload["process"] = process_name

        output_path = self.recorder.output_path
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            with open(output_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=4)
            self.config_var.set(self._to_display_path(output_path))
            self.status_var.set("录制完成")
            trigger_key = next(iter(payload))
            action_count = len(payload[trigger_key]["actions"])
            self._queue_log(f"录制完成：触发键 {trigger_key}，共生成 {action_count} 个动作")
            self._queue_log(f"配置已覆盖写入：{output_path}")
        except Exception as exc:
            self.status_var.set("录制保存失败")
            self._queue_log(f"保存录制配置失败: {exc}")

    def _on_print_processes(self):
        if self.process_dump_thread and self.process_dump_thread.is_alive():
            self._queue_log("进程列表正在生成，请稍候...")
            return

        self.status_var.set("正在打印窗口进程...")
        self._queue_log("开始打印当前可见窗口对应的进程...")
        self.process_dump_thread = threading.Thread(target=self._dump_processes_worker, daemon=True)
        self.process_dump_thread.start()

    def _dump_processes_worker(self):
        try:
            window_rows: list[tuple[str, int, str, str, str]] = []
            unique_names: set[str] = set()

            def _window_callback(hwnd, lparam):
                if not ctypes.windll.user32.IsWindowVisible(hwnd):
                    return True

                title_len = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if title_len <= 0:
                    return True

                title_buffer = ctypes.create_unicode_buffer(title_len + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
                title = title_buffer.value
                if not title:
                    return True

                class_buffer = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
                class_name = class_buffer.value

                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc_name = ""
                if pid.value:
                    try:
                        proc_name = psutil.Process(pid.value).name() or ""
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        proc_name = "Unknown"

                config_name = self._normalize_process_name(proc_name)
                window_rows.append((proc_name.lower(), pid.value, proc_name, class_name, title))
                if config_name:
                    unique_names.add(config_name)
                return True

            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_window_callback)
            ctypes.windll.user32.EnumWindows(enum_windows_proc, 0)

            window_rows.sort(key=lambda item: (item[0], item[1], item[4]))
            sorted_unique_names = sorted(unique_names)

            self._queue_log("===== 可见窗口对应进程开始 =====")
            for _sort_name, pid, raw_name, class_name, title in window_rows:
                config_name = self._normalize_process_name(raw_name)
                self._queue_log(
                    f"HWND窗口进程 PID: {pid}, 进程: {raw_name}, 配置名: {config_name}, CLASS: {class_name}, 标题: '{title}'"
                )
            self._queue_log("===== 可见窗口对应进程结束 =====")

            self._queue_log("===== 可直接复制的进程名（来自可见窗口，去重） =====")
            for name in sorted_unique_names:
                self._queue_log(name)
            self._queue_log("===== 可直接复制的进程名结束 =====")

            self.root.after(
                0,
                lambda: self.status_var.set(
                    f"已打印 {len(window_rows)} 个可见窗口进程，去重后 {len(sorted_unique_names)} 个进程名"
                ),
            )
        except Exception as exc:
            self._queue_log(f"打印进程失败: {exc}")
            self.root.after(0, lambda: self.status_var.set("打印进程失败"))

    def _normalize_process_name(self, name: str) -> str:
        if not name:
            return ""
        base = os.path.basename(name).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        return base

    def _start_manager(self):
        config_value = self.config_var.get().strip()
        if not config_value:
            messagebox.showwarning("参数缺失", "请提供配置文件路径。")
            return
        abs_config = os.path.abspath(config_value) if os.path.isabs(config_value) else os.path.abspath(
            os.path.join(self.project_root, config_value)
        )
        if not os.path.isfile(abs_config):
            messagebox.showwarning("无效配置", f"找不到配置文件: {config_value}")
            return
        if not abs_config.lower().endswith(".json"):
            messagebox.showwarning("无效配置", "配置文件应以 .json 结尾。")
            return
        self.config_var.set(self._to_display_path(abs_config))

        manager = AutoInputManager(abs_config, self.log_var.get(), process_name=self.process_var.get() or None)
        self.manager = manager
        self.action_text.set("停止")
        self.status_var.set("运行中")

        self.worker_thread = threading.Thread(target=self._run_manager, args=(manager,), daemon=True)
        self.worker_thread.start()

    def _stop_manager(self):
        if not self.manager:
            return
        self.status_var.set("正在停止...")
        self._queue_log("正在停止 AutoInputManager...")
        try:
            self.manager.stop()
        except Exception as exc:
            self._queue_log(f"停止失败: {exc}")

    def _run_manager(self, manager: AutoInputManager):
        try:
            with PrintForwarder(self.log_queue):
                manager.start()
        except Exception as exc:  # pragma: no cover
            self._queue_log(f"运行出错: {exc}")
        finally:
            self.manager = None
            self.worker_thread = None
            self.root.after(0, self._on_worker_exit)

    def _on_worker_exit(self):
        self.action_text.set("启动")
        self.status_var.set("空闲")
        if self.want_close:
            self.root.destroy()

    def _poll_logs(self):
        at_bottom = self.log_text.yview()[1] >= 0.99
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message)
            self.log_text.config(state=tk.DISABLED)
            if at_bottom:
                self.log_text.yview_moveto(1.0)
        self.root.after(100, self._poll_logs)

    def _on_close(self):
        if self.recorder.recording:
            self.recorder.stop()
        if self.manager:
            self.want_close = True
            self._stop_manager()
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
