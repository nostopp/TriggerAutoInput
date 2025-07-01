import json
import sys
from random import random as rand
from pynput import keyboard, mouse
import pydirectinput
import time
from typing import Dict, List, Union, Optional
import threading

# LAST_TIME = time.perf_counter()
MOUSE_BUTTON = set(['left', 'right', 'middle', 'x1', 'x2'])

class AutoInputManager:
    def __init__(self, config_path: str, open_log: bool):
        self.config_path = config_path
        self.config = self.load_config()
        self.open_log = open_log
        self.keyboard_listener = None
        self.mouse_listener = None
        
        self._ctrl_pressed = False
        self._shift_pressed = False

        self._is_running = threading.Event()
        self._events_paused = threading.Event()
        
        self._loops_lock = threading.Lock()  # 保护 active_loops
        self._keys_lock = threading.Lock()   # 保护 pressed_keys
        self.active_loops = {}
        self.pressed_keys = set()

        self.active_threads = []  # 追踪所有活动的线程
        self.thread_lock = threading.Lock()  # 线程列表的同步锁

    @property
    def is_running(self):
        return self._is_running.is_set()

    @property
    def events_paused(self):
        return self._events_paused.is_set()

    def load_config(self) -> dict:
        """加载配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            sys.exit(1)

    def execute_action(self, action: dict, press_keys: Dict[str, bool]):
        """执行单个动作"""
        if self.open_log:
            print(f'执行动作: {action.get('type')}, {action.get('action', None)}')

        action_type = action.get('type')
        if action_type == 'keyboard':
            key = action.get('key')
            if action.get('action') == 'press':
                if key not in press_keys:
                    pydirectinput.keyDown(key, _pause=False)
                    press_keys[key] = True
                elif self.open_log:
                    print(f'按键已按下，未重复触发: {key}')
            elif action.get('action') == 'release':
                pydirectinput.keyUp(key, _pause=False)
                press_keys.pop(key, None)
            elif action.get('action') == 'click':
                pydirectinput.press(key, _pause=False)
        elif action_type == 'mouse':
            key = action.get('key')
            if action.get('action') == 'click':
                pydirectinput.click(button=key, _pause=False)
            elif action.get('action') == 'press':
                if key not in press_keys:
                    pydirectinput.mouseDown(button=key, _pause=False)
                    press_keys[key] = True
                elif self.open_log:
                    print(f'按键已按下，未重复触发: {key}')
            elif action.get('action') == 'release':
                pydirectinput.mouseUp(button=key, _pause=False)
                press_keys.pop(key, None)
        elif action_type == 'delay':
            random = action.get('random', 0)
            time.sleep(action.get('duration', 0.1) + random * rand())

    def execute_actions(self, actions: List[dict], press_keys: Dict[str, bool]):
        """执行一系列动作"""
        # global LAST_TIME
        # print(f'大循环间隔用时: {time.perf_counter() - LAST_TIME}')
        for action in actions:
            # LAST_TIME = time.perf_counter()
            self.execute_action(action, press_keys)
            # print(f'步骤用时: {time.perf_counter() - LAST_TIME}')
            # LAST_TIME = time.perf_counter()

    def wrap_thread_function(self, func, *args):
        """包装线程函数，确保线程完成后从活动线程列表中移除"""
        def wrapper():
            try:
                func(*args)
            except Exception as e:
                if self.open_log:
                    print(f"线程执行出错: {e}")
            finally:
                current_thread = threading.current_thread()
                with self.thread_lock:
                    if current_thread in self.active_threads:
                        self.active_threads.remove(current_thread)
        return wrapper

    def _trigger_action(self, actions: List[dict]):
        # 一次性触发不需要锁
        press_keys = {}
        self.execute_actions(actions, press_keys)
        for key in press_keys.keys():
            if key in MOUSE_BUTTON:
                pydirectinput.mouseUp(button=key, _pause=False)
            else:
                pydirectinput.keyUp(key, _pause=False)

    def _loop_trigger_actions(self, actions: List[dict], trigger_key: str):
        press_keys = {}
        while self.is_running:
            # 检查循环状态时使用短暂的锁
            with self._loops_lock:
                if not self.active_loops.get(trigger_key, False):
                    break
            self.execute_actions(actions, press_keys)

        for key in press_keys.keys():
            if key in MOUSE_BUTTON:
                pydirectinput.mouseUp(button=key, _pause=False)
            else:
                pydirectinput.keyUp(key, _pause=False)

    def handle_trigger(self, trigger_key: str, is_press: bool = True):
        """处理触发事件"""
        if trigger_key not in self.config:
            return

        if self.events_paused:
            return

        trigger_config = self.config[trigger_key]
        trigger_type = trigger_config.get('trigger_type', 'press_once')
        actions = trigger_config.get('actions', [])

        if trigger_type == 'once' and is_press:
            thread = threading.Thread(target=self.wrap_thread_function(self._trigger_action, actions), daemon=True)
            with self.thread_lock:
                self.active_threads.append(thread)
            if self.open_log:
                print(f'Trigger: {trigger_key}, Type: {trigger_type}')
            thread.start()
        elif trigger_type == 'hold':
            if is_press:
                should_start = False
                with self._loops_lock:
                    if trigger_key not in self.active_loops:
                        self.active_loops[trigger_key] = True
                        should_start = True
                
                if should_start:
                    thread = threading.Thread(target=self.wrap_thread_function(self._loop_trigger_actions, actions, trigger_key), daemon=True)
                    with self.thread_lock:
                        self.active_threads.append(thread)
                    if self.open_log:
                        print(f'Trigger: {trigger_key}, Type: {trigger_type}')
                    thread.start()
            else:
                if self.open_log:
                    print(f'Trigger Stop: {trigger_key}, Type: {trigger_type}')
                with self._loops_lock:
                    self.active_loops.pop(trigger_key, None)
        elif trigger_type == 'toggle':
            if is_press:
                should_start = False
                with self._loops_lock:
                    if trigger_key not in self.active_loops:
                        self.active_loops[trigger_key] = True
                        should_start = True
                    else:
                        if self.open_log:
                            print(f'Trigger Stop: {trigger_key}, Type: {trigger_type}')
                        self.active_loops.pop(trigger_key)
                
                if should_start:
                    thread = threading.Thread(target=self.wrap_thread_function(self._loop_trigger_actions, actions, trigger_key), daemon=True)
                    with self.thread_lock:
                        self.active_threads.append(thread)
                    if self.open_log:
                        print(f'Trigger: {trigger_key}, Type: {trigger_type}')
                    thread.start()

    def on_keyboard_press(self, key):
        """键盘事件按下处理"""
        # 检查修饰键
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = True
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self._shift_pressed = True

        key_name = key.char if hasattr(key, 'char') else None
        
        # 检查暂停/恢复快捷键
        if key_name in ('x', 'X', '\x18'):
            if self._ctrl_pressed and self._shift_pressed:
                if self._events_paused.is_set():
                    self._events_paused.clear()
                else:
                    self._events_paused.set()
                    with self._loops_lock:
                        self.active_loops.clear()
                print(f'事件响应已{"暂停" if self.events_paused else "恢复"}')
                return

        if self.events_paused:
            return

        if key_name:
            should_trigger = False
            with self._keys_lock:
                if key_name not in self.pressed_keys:
                    if self.open_log:
                        print(f'keyDown {key_name}')
                    self.pressed_keys.add(key_name)
                    should_trigger = True
            
            # 在锁外触发事件
            if should_trigger:
                self.handle_trigger(f'keyboard_{key_name}', True)

    def on_keyboard_release(self, key):
        """键盘事件抬起处理"""
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = False
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self._shift_pressed = False

        if self.events_paused:
            return

        key_name = key.char if hasattr(key, 'char') else None
        if key_name:
            with self._keys_lock:
                if self.open_log:
                    print(f'keyUp {key_name}')
                self.pressed_keys.discard(key_name)
            # 在锁外触发事件
            self.handle_trigger(f'keyboard_{key_name}', False)

    def on_mouse_click(self, x, y, button, pressed):
        """鼠标点击事件处理"""
        # 如果事件已暂停，不处理鼠标事件
        if self.events_paused:
            return

        button_name = button.name if hasattr(button, 'name') else None
        if button_name:
            if self.open_log:
                print(f'mouse_{button_name} {"down" if pressed else "up"}')
            self.handle_trigger(f'mouse_{button_name}', pressed)

    def start(self):
        """启动监听"""
        self._is_running.set()
        self.keyboard_listener = keyboard.Listener(on_press=self.on_keyboard_press, on_release=self.on_keyboard_release)
        self.mouse_listener = mouse.Listener(on_click=self.on_mouse_click)
        
        self.keyboard_listener.start()
        self.mouse_listener.start()

        print('启动监听, 按 Ctrl+Shift+x 暂停/恢复监听')
        
        try:
            while self.is_running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止监听并清理所有正在运行的操作"""
        print("正在停止所有操作...")
        self._is_running.clear()
        
        # 清理所有活动的循环
        with self._loops_lock:
            self.active_loops.clear()
            
        # 停止所有监听器
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        
        # 确保监听器完全停止
        if self.keyboard_listener:
            self.keyboard_listener.join(2)
        if self.mouse_listener:
            self.mouse_listener.join(2)

        # 等待所有操作线程结束
        for thread in self.active_threads:
            try:
                thread.join()
            except:
                pass  # 忽略超时异常
        
        # 清理线程列表
        self.active_threads.clear()
            
        print("所有操作已停止")