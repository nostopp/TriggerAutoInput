import argparse
from auto_input_manager import AutoInputManager

def parse_args():
    parser = argparse.ArgumentParser(description='自动输入工具 - 通过配置文件实现键鼠事件的自动化操作',formatter_class=argparse.RawDescriptionHelpFormatter,epilog='''
示例:
  python main.py example.json  # 使用示例配置文件启动
  python main.py example.json --log  # 启用详细日志输出
  
快捷键:
  Ctrl+Shift+X  # 暂停/恢复事件响应和自动操作

配置文件格式说明请参考 README.md
''')
    
    parser.add_argument('config', type=str, help='配置文件，必须是有效的 JSON 文件')
    parser.add_argument('--log', action='store_true', help='启用详细日志输出')
    
    return parser.parse_args()

def main():
    args = parse_args()
    config = f'config/{args.config}'
    manager = AutoInputManager(config, args.log)
    manager.start()

if __name__ == "__main__":
    main()