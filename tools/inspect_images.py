"""检测图片真实 mode 与位深，不修改任何文件。

用法：python -m tools.inspect_images [路径...]
不传路径时，扫描项目内的 demo/ 和 测试图片/ 目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}


def bit_depth(image: Image.Image) -> str:
    """根据 mode 返回每通道位深说明，并附带实际数组 dtype。"""
    mode = image.mode
    array = np.array(image)
    dtype = str(array.dtype)
    if mode == "I;16":
        return f"16-bit 灰度（mode=I;16, dtype={dtype}）"
    if mode == "I":
        return f"32-bit 灰度（mode=I, dtype={dtype}）"
    if mode == "F":
        return f"32-bit 浮点（mode=F, dtype={dtype}）"
    if mode in ("RGB", "RGBA", "L", "LA", "P", "PA", "CMYK"):
        return f"8-bit（mode={mode}, dtype={dtype}）"
    return f"其他（mode={mode}, dtype={dtype}）"


def inspect_path(path: Path) -> None:
    """扫描一个文件或目录并打印每张图的 mode 与位深。"""
    files: list[Path] = []
    if path.is_file():
        files = [path]
    else:
        for item in sorted(path.rglob("*")):
            if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(item)
    if not files:
        print(f"未在 {path} 下找到图片文件。")
        return
    print(f"扫描 {path}：共 {len(files)} 个图片文件")
    print("-" * 72)
    for item in files:
        try:
            with Image.open(item) as image:
                detail = bit_depth(image)
                print(f"{item.relative_to(path) if item.is_relative_to(path) else item}\n    {image.mode} / {image.size} / {detail}")
        except Exception as error:
            print(f"{item}\n    读取失败：{error}")
    print()


def main() -> None:
    """按命令行参数或默认目录执行检测。"""
    targets = [Path(argument) for argument in sys.argv[1:]]
    if not targets:
        for name in ("demo", "测试图片"):
            candidate = ROOT / name
            if candidate.exists():
                targets.append(candidate)
    if not targets:
        print("未指定路径，也没有默认目录。请传入图片文件或目录路径。")
        return
    for target in targets:
        if target.exists():
            inspect_path(target)
        else:
            print(f"路径不存在：{target}")


if __name__ == "__main__":
    main()
