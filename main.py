"""混淆图工具启动入口，也用于 PyInstaller 单文件打包。"""

import sys

from confusing_images.gui import run


def main() -> None:
    """启动中文图形界面，支持通过命令行传入可选图片路径。"""
    run(sys.argv[1] if len(sys.argv) > 1 else None)


if __name__ == "__main__":
    main()
