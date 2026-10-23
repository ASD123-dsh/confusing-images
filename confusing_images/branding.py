"""生成程序图标，共用于窗口和 Windows EXE，不依赖外部资源。"""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


def create_icon() -> Image.Image:
    """绘制蓝色圆角底和错落图块，缩小时保持图形清晰。"""
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=54, fill="#2563EB")
    draw.rounded_rectangle((54, 53, 119, 118), radius=8, fill="#FFFFFF")
    draw.rounded_rectangle((137, 45, 202, 110), radius=8, fill="#BFD5FF")
    draw.rounded_rectangle((44, 137, 109, 202), radius=8, fill="#BFD5FF")
    draw.rounded_rectangle((127, 128, 192, 193), radius=8, fill="#F8CC73")
    return image


def main() -> None:
    """为打包脚本生成多分辨率 ICO 文件。"""
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    create_icon().save(output, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])


if __name__ == "__main__":
    main()
