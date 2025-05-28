import argparse
from auto_input_manager import AutoInputManager

def parse_args():
    parser = argparse.ArgumentParser(description='自动输入工具 - 通过配置文件实现键鼠事件的自动化操作',formatter_class=argparse.RawDescriptionHelpFormatter,epilog='''
示例:
  python main.py example.json  # 使用示例配置文件启动

配置文件格式说明请参考 README.md
''')
    
    parser.add_argument('config',type=str,help='配置文件，必须是有效的 JSON 文件')
    parser.add_argument('--log',default=False,help='开启日志',action="store_true")
    
    return parser.parse_args()

def main():
    args = parse_args()
    config = f'config/{args.config}'
    manager = AutoInputManager(config, args.log)
    manager.start()

if __name__ == "__main__":
    main()