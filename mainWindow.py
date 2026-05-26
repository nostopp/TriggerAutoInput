import builtins
import os
import queue
import threading
import sys
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import psutil

from auto_input_manager import AutoInputManager


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
