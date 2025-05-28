import json
import sys
from random import random as rand
from pynput import keyboard, mouse
import pydirectinput
import time
from typing import Dict, List, Union, Optional
import threading

class AutoInputManager:
    def __init__(self, config_path: str, open_log: bool):
        self.config_path = config_path
        self.config = self.load_config()
        self.open_log = open_log
        self.keyboard_listener = None
        self.mouse_listener = None
        self.is_running = True
        self.active_loops = {}  # 存储正在运行的循环任务
        self.pressed_keys = set()  # 追踪当前按下的键
        # 追踪修饰键状态
        self.ctrl_pressed = False
        self.shift_pressed = False

    def load_config(self) -> dict:
        """加载配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            sys.exit(1)

    def execute_action(self, action: dict):
        """执行单个动作"""
        action_type = action.get('type')
        if action_type == 'keyboard':
            key = action.get('key')
            if action.get('action') == 'press':
                pydirectinput.keyDown(key)
            elif action.get('action') == 'release':
                pydirectinput.keyUp(key)
            elif action.get('action') == 'click':
                pydirectinput.press(key)
        elif action_type == 'mouse':
            key = action.get('key')
            if action.get('action') == 'click':
                pydirectinput.click(button=key)
            elif action.get('action') == 'press':
                pydirectinput.mouseDown(button=key)
            elif action.get('action') == 'release':
                pydirectinput.mouseUp(button=key)
        elif action_type == 'delay':
            random = action.get('random', 0)
            time.sleep(action.get('duration', 0.1) + random * rand())

    def execute_actions(self, actions: List[dict]):
        """执行一系列动作"""
        for action in actions:
            self.execute_action(action)

    def handle_trigger(self, trigger_key: str, is_press: bool = True):
        """处理触发事件"""
        if trigger_key not in self.config:
            return

        trigger_config = self.config[trigger_key]
        trigger_type = trigger_config.get('trigger_type', 'press_once')
        actions = trigger_config.get('actions', [])

        if trigger_type == 'once' and is_press:
            # 一次性触发
            def loop_actions():
                self.execute_actions(actions)
            threading.Thread(target=loop_actions, daemon=True).start()
            if self.open_log:
                print(f'Trigger: {trigger_key}, Type: {trigger_type}')
        elif trigger_type == 'hold':
            # 按住时循环执行，松开时停止
            if is_press:
                if trigger_key not in self.active_loops:
                    # 启动循环
                    self.active_loops[trigger_key] = True
                    def loop_actions():
                        while self.active_loops.get(trigger_key, False):
                            self.execute_actions(actions)
                    threading.Thread(target=loop_actions, daemon=True).start()
                    if self.open_log:
                        print(f'Trigger: {trigger_key}, Type: {trigger_type}')
            elif trigger_key in self.active_loops:
                # 松开时停止循环
                self.active_loops.pop(trigger_key)
                if self.open_log:
                    print(f'Trigger Stop: {trigger_key}, Type: {trigger_type}')
        elif trigger_type == 'toggle':
            if is_press:
                if trigger_key not in self.active_loops:
                    # 启动循环
                    self.active_loops[trigger_key] = True
                    def loop_actions():
                        while self.active_loops.get(trigger_key, False):
                            self.execute_actions(actions)
                    threading.Thread(target=loop_actions, daemon=True).start()
                    if self.open_log:
                        print(f'Trigger: {trigger_key}, Type: {trigger_type}')

                elif trigger_key in self.active_loops:
                    # 停止循环
                    self.active_loops.pop(trigger_key)
                    if self.open_log:
                        print(f'Trigger Stop: {trigger_key}, Type: {trigger_type}')

    def on_keyboard_press(self, key):
        """键盘事件按下处理"""
        # 检查修饰键
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = True
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self.shift_pressed = True

        key_name = key.char if hasattr(key, 'char') else None

        if key_name == '\x18' and self.ctrl_pressed and self.shift_pressed:
            # 检查强制停止快捷键
            print("检测到强制停止快捷键 (Shift+Ctrl+X)，正在停止所有操作...")
            self.stop()
            return

        if key_name and key_name not in self.pressed_keys:
            if self.open_log:
                print(f'keyDown {key_name}')
            self.pressed_keys.add(key_name)
            self.handle_trigger(f'keyboard_{key_name}', True)

    def on_keyboard_release(self, key):
        """键盘事件抬起处理"""
        # 更新修饰键状态
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = False
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self.shift_pressed = False

        key_name = key.char if hasattr(key, 'char') else None
        if key_name:
            if self.open_log:
                print(f'keyUp {key_name}')
            self.pressed_keys.discard(key_name)
            self.handle_trigger(f'keyboard_{key_name}', False)

    def on_mouse_click(self, x, y, button, pressed):
        """鼠标点击事件处理"""
        button_name = button.name if hasattr(button, 'name') else None
        if button_name:
            if self.open_log:
                print(f'mouse_{button_name} {"down" if pressed else "up"}')
            self.handle_trigger(f'mouse_{button_name}', pressed)

    def start(self):
        """启动监听"""
        self.keyboard_listener = keyboard.Listener(on_press=self.on_keyboard_press, on_release=self.on_keyboard_release)
        self.mouse_listener = mouse.Listener(on_click=self.on_mouse_click)
        
        self.keyboard_listener.start()
        self.mouse_listener.start()
        
        try:
            while self.is_running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止监听并清理所有正在运行的操作"""
        print("正在停止所有操作...")
        self.is_running = False
        # 清理所有活动的循环
        self.active_loops.clear()
        # 停止所有监听器
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        # 释放所有可能被按住的按键
        for key in list(self.pressed_keys):
            try:
                pydirectinput.keyUp(key)
            except:
                pass
        print("所有操作已停止")