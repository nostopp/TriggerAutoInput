import builtins
import os
import queue
import threading
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from auto_input_manager import AutoInputManager


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
        self.project_root = os.path.abspath(os.getcwd())
        self.log_queue: queue.Queue = queue.Queue()
        self.manager: AutoInputManager | None = None
        self.worker_thread: threading.Thread | None = None
        self.want_close = False
        self.action_text = tk.StringVar(value="启动")
        self.status_var = tk.StringVar(value="空闲")
        self.config_var = tk.StringVar(value="config/example.json")
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

        ttk.Label(param_frame, text="配置文件:").grid(row=0, column=0, sticky=tk.W)
        self.config_entry = ttk.Entry(param_frame, textvariable=self.config_var, width=40)
        self.config_entry.grid(row=0, column=1, sticky=tk.W, padx=(4, 0))
        self.browse_button = ttk.Button(param_frame, text="浏览", command=self._on_browse)
        self.browse_button.grid(row=0, column=2, sticky=tk.W, padx=(4, 0))

        ttk.Label(param_frame, text="启用日志:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Checkbutton(param_frame, variable=self.log_var).grid(row=1, column=1, sticky=tk.W, pady=(8, 0))

        ttk.Label(param_frame, text="进程名 (可选):").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        self.process_entry = ttk.Entry(param_frame, textvariable=self.process_var, width=40)
        self.process_entry.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=(4, 0), pady=(8, 0))

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

    def _on_browse(self):
        selected = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=f'{self.project_root}/config',
            filetypes=[
                ("JSON 文件", "*.json"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        if os.path.commonpath([self.project_root, selected]) == self.project_root:
            selected = os.path.relpath(selected, self.project_root)
        self.config_var.set(selected)

    def _on_action(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._stop_manager()
        else:
            self._start_manager()

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
        if os.path.commonpath([self.project_root, abs_config]) == self.project_root:
            rel_path = os.path.relpath(abs_config, self.project_root)
            self.config_var.set(rel_path)
        else:
            self.config_var.set(abs_config)

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
        self.log_queue.put("正在停止 AutoInputManager...\n")
        try:
            self.manager.stop()
        except Exception as exc:
            self.log_queue.put(f"停止失败: {exc}\n")

    def _run_manager(self, manager: AutoInputManager):
        try:
            with PrintForwarder(self.log_queue):
                manager.start()
        except Exception as exc:  # pragma: no cover
            self.log_queue.put(f"运行出错: {exc}\n")
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
