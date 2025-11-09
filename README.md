# TriggerAutoInput

一个基于Python的自动输入工具，可以通过自定义配置文件来设置触发条件和自动化操作。

## 功能

- 支持键盘和鼠标事件监听
- 支持多种触发方式：
  - once：按下触发一次
  - hold：按住持续触发
  - toggle：点击切换开关状态
- 支持多种自动化操作：
  - 键盘、鼠标按键（按下、松开、点击）
  - 延时操作
- ctrl+shift+x 暂停/恢复触发事件监听

## 使用方法

1. 确保已安装必要的依赖：
   ```bash
   uv sync
   ```

2. 创建配置文件（JSON格式，放在config文件夹，已有示例example.json），例如 `config.json`：
   ```json
   {
       "keyboard_f": {
           "trigger_type": "once",
           "actions": [
               {
                   "type": "keyboard",
                   "action": "click",
                   "key": "1"
               }
           ]
       }
   }
   ```

3. 运行程序：
   ```bash
   uv run main.py config.json
   ```
    可接受参数
    * --log, 启用详细日志输出
    * -p name / --process name, 指定前台进程名，仅当该进程在前台时才响应事件

## 配置文件格式

触发键的格式：
- 键盘按键：`keyboard_<key>`（例如：keyboard_f, keyboard_space）
- 鼠标按键：`mouse_<button>`（例如：mouse_left, mouse_right）

配置项说明：
```json
{
    "触发键": {
        "trigger_type": "触发类型",  // once, hold, 或 toggle
        "actions": [
            {
                "type": "动作类型",  // keyboard, mouse, 或 delay
                "action": "具体操作", // press, release, click(具体取决于type)
                "key": "按键",      // 当type为keyboard, mouse时需要
                "duration": 0.1,    // 当type为delay时需要，单位为秒
                "random": 0.1,    // 随机延时，当type为delay时可选，单位为秒
            }
        ]
    }
}
```