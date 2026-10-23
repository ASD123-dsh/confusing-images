"""ObfuscationUtils 算法的 Python 移植及独立的原格式兼容实现。

改编自 https://github.com/2195517546/ObfuscationUtils
固定版本 b573e0051d375a7a4c7ccfe7d7e385997b52348b。
Copyright (c) 2025 uiloalxise，MIT 许可证，完整声明见 third_party.py。
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from typing import Callable

import numpy as np
from PIL import Image

from .errors import ImageToolError

ALGORITHMS = (
    ("tomato", "Tomato · 小番茄", "兼容密钥：有限数字，例如 0.618；沿 Gilbert 曲线移动像素。"),
    ("block", "Block · 分块混淆", "兼容密钥：任意字符串；固定 32×32 块，必要时补边。"),
    ("row-pixel", "RowPixel · 行内像素", "兼容密钥：任意字符串；每行内部置换像素。"),
    ("per-pixel", "PerPixel · 二维像素", "兼容密钥：任意字符串；同时置换横纵像素位置。"),
    ("pic-row", "PicEncryptRow · 行混淆", "兼容密钥：0 到 1 之间的小数，例如 0.618，不包含端点。"),
    ("pic-row-column", "PicEncrypt · 行列混淆", "兼容密钥：0 到 1 之间的小数；先行后列，还原时顺序相反。"),
    ("sort", "Sort · 排序列混淆", "兼容模式不需要密钥，但末行被覆盖且颜色有损；本工具模式保留全部像素。"),
    ("random", "Random · 随机列混淆", "兼容模式：整数种子或字符串；末行有损，还原时原项目不验证种子。"),
)
ALGORITHM_IDS = tuple(item[0] for item in ALGORITHMS)
NUMERIC_ALGORITHMS = ("tomato", "pic-row", "pic-row-column")
MANUAL_DEFAULT_KEYS = dict((("tomato", "1"), ("pic-row", "0.114514"), ("pic-row-column", "0.1145")))


def manual_restore_key(algorithm: str, key: str) -> str:
    """无参数图片留空时采用公开默认值；非空输入保持原样，不偷偷替换错误密钥。"""
    if algorithm not in ALGORITHM_IDS:
        raise ImageToolError("请选择正确的手动还原算法。")
    if not isinstance(key, str):
        raise ImageToolError("手动还原密钥必须为字符串，可以留空使用界面显示的默认参数。")
    return key if key else MANUAL_DEFAULT_KEYS.get(algorithm, "")


def _report(progress: Callable | None, value: float, message: str) -> None:
    """将算法进度交给调用者，不直接访问图形界面。"""
    if progress is not None:
        progress(value, message)


def _numeric_key(key: str, logistic: bool = False) -> float:
    """检查原项目数值密钥，拒绝非有限及会退化的 Logistic 参数。"""
    try:
        value = float(key)
    except (ValueError, TypeError, OverflowError) as error:
        raise ImageToolError("此兼容算法需要数值密钥，例如 0.618。") from error
    if not math.isfinite(value):
        raise ImageToolError("数值密钥不能是 NaN 或无穷大。")
    if logistic and not 0 < value < 1:
        raise ImageToolError("PicEncrypt 兼容密钥必须大于 0 且小于 1，例如 0.618。")
    return value


def native_key(algorithm: str, shuffle_key: bytes) -> str:
    """为带校验的本工具格式派生算法密钥，不保存用户原口令。"""
    if algorithm in NUMERIC_ALGORITHMS:
        value = (int.from_bytes(shuffle_key[:6], "big") + 0.5) / (1 << 48)
        return repr(value)
    if algorithm == "random":
        return str(int.from_bytes(shuffle_key[:8], "big", signed=True))
    return shuffle_key.hex()


def _md5_shuffle(length: int, key: str) -> np.ndarray:
    """复现 Java 基于 MD5 前七个十六进制位的 Fisher-Yates 置换。"""
    order = np.arange(length, dtype=np.intp)
    for index in range(length - 1, 0, -1):
        digest = hashlib.md5((key + str(index)).encode("utf-8")).digest()
        selected = (int.from_bytes(digest[:4], "big") >> 4) % (index + 1)
        order[index], order[selected] = order[selected], order[index]
    return order


def _java_seed(key: str) -> int:
    """复现 Long.parseLong 及失败后的 String.hashCode，包括 UTF-16 代理对。"""
    if re.fullmatch(r"[+-]?[0-9]+", key):
        try:
            value = int(key)
            if -(1 << 63) <= value < (1 << 63):
                return value
        except ValueError:
            pass
    encoded = key.encode("utf-16-be")
    value = 0
    for index in range(0, len(encoded), 2):
        value = (31 * value + int.from_bytes(encoded[index:index + 2], "big")) & 0xFFFFFFFF
    return value if value < (1 << 31) else value - (1 << 32)


class JavaRandom:
    """仅用于精确复现 java.util.Random，不用于安全随机数。"""

    def __init__(self, seed: int) -> None:
        """按 Java 的 48 位种子扰动规则初始化。"""
        self.seed = (seed ^ 0x5DEECE66D) & ((1 << 48) - 1)

    def _next(self) -> int:
        """更新线性同余状态并取得 31 位整数。"""
        self.seed = (self.seed * 0x5DEECE66D + 11) & ((1 << 48) - 1)
        return self.seed >> 17

    def next_int(self, bound: int) -> int:
        """复现有界整数及 Java 有符号溢出条件下的拒绝采样。"""
        if bound & (bound - 1) == 0:
            return (bound * self._next()) >> 31
        while True:
            bits = self._next()
            value = bits % bound
            if bits - value + bound - 1 < (1 << 31):
                return value


def _random_order(width: int, key: str) -> np.ndarray:
    """复现 Collections.shuffle(List, new Random(seed)) 的列顺序。"""
    random = JavaRandom(_java_seed(key))
    order = np.arange(width, dtype=np.intp)
    for size in range(width, 1, -1):
        selected = random.next_int(size)
        order[size - 1], order[selected] = order[selected], order[size - 1]
    return order


_gilbert_cache: dict[tuple[int, int], np.ndarray] = {}
_gilbert_cache_lock = threading.Lock()


def _gilbert_path_uncached(width: int, height: int, progress: Callable | None) -> np.ndarray:
    """复现原项目广义 Hilbert/Gilbert 曲线，使用数组批量输出直线段。"""
    positions = np.empty(width * height, dtype=np.intp)
    position = 0
    next_report = 0

    def generate(x: int, y: int, ax: int, ay: int, bx: int, by: int) -> None:
        """按原 Java floorDiv 规则递归划分空间填充曲线。"""
        nonlocal position, next_report
        w, h = abs(ax + ay), abs(bx + by)
        dax, day = (ax > 0) - (ax < 0), (ay > 0) - (ay < 0)
        dbx, dby = (bx > 0) - (bx < 0), (by > 0) - (by < 0)
        if h == 1 or w == 1:
            length = w if h == 1 else h
            dx, dy = (dax, day) if h == 1 else (dbx, dby)
            positions[position:position + length] = x + y * width + np.arange(length, dtype=np.intp) * (dx + dy * width)
            position += length
            if position >= next_report:
                _report(progress, 0.7 * position / len(positions), "正在生成 Gilbert 曲线路径")
                next_report = position + 65_536
            return
        ax2, ay2, bx2, by2 = ax // 2, ay // 2, bx // 2, by // 2
        w2, h2 = abs(ax2 + ay2), abs(bx2 + by2)
        if 2 * w > 3 * h:
            if w2 & 1 and w > 2:
                ax2, ay2 = ax2 + dax, ay2 + day
            generate(x, y, ax2, ay2, bx, by)
            generate(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by)
        else:
            if h2 & 1 and h > 2:
                bx2, by2 = bx2 + dbx, by2 + dby
            generate(x, y, bx2, by2, ax2, ay2)
            generate(x + bx2, y + by2, ax, ay, bx - bx2, by - by2)
            generate(x + ax - dax + bx2 - dbx, y + ay - day + by2 - dby, -bx2, -by2, -(ax - ax2), -(ay - ay2))

    if width >= height:
        generate(0, 0, width, 0, 0, height)
    else:
        generate(0, 0, 0, height, width, 0)
    if position != len(positions):
        raise ImageToolError("Gilbert 路径生成失败。")
    return positions


def _gilbert_positions(width: int, height: int, progress: Callable | None) -> np.ndarray:
    """按尺寸缓存曲线路径，同一尺寸的图片只计算一次，复用不随密钥变化的结果。"""
    key = (width, height)
    cached = _gilbert_cache.get(key)
    if cached is not None:
        return cached
    with _gilbert_cache_lock:
        cached = _gilbert_cache.get(key)
        if cached is None:
            cached = _gilbert_path_uncached(width, height, progress)
            # 限定缓存条目，避免批量处理大量不同尺寸时无限占用内存。
            if len(_gilbert_cache) >= 64:
                _gilbert_cache.pop(next(iter(_gilbert_cache)))
            _gilbert_cache[key] = cached
        return cached


def _tomato(pixels: np.ndarray, key: str, inverse: bool, progress: Callable | None) -> np.ndarray:
    """在 Gilbert 曲线上按黄金比例计算的偏移量循环移动像素。"""
    value = _numeric_key(key)
    height, width, channels = pixels.shape
    count = width * height
    raw = (math.sqrt(5) - 1) / 2 * count * value
    if raw >= (1 << 63) - 1:
        offset = (1 << 63) - 1
    elif raw <= -(1 << 63):
        offset = -(1 << 63)
    else:
        lower = math.floor(raw)
        offset = lower + int(raw - lower >= 0.5)
    offset %= count
    positions = _gilbert_positions(width, height, progress)
    flat = pixels.reshape(count, channels)
    result = np.empty_like(flat)
    result[positions] = np.roll(flat[positions], -offset if inverse else offset, axis=0)
    return result.reshape(pixels.shape)


def _pixel_transform(pixels: np.ndarray, key: str, inverse: bool, two_axes: bool, progress: Callable | None) -> np.ndarray:
    """分批复现 RowPixel/PerPixel 的二维索引，避免为整张大图分配索引矩阵。"""
    height, width = pixels.shape[:2]
    x_order = _md5_shuffle(width, key)
    y_order = _md5_shuffle(height, key) if two_axes else None
    result = np.empty_like(pixels)
    columns = np.arange(width, dtype=np.intp)[None, :]
    batch = max(1, min(128, 1_000_000 // width))
    for begin in range(0, height, batch):
        rows = np.arange(begin, min(height, begin + batch), dtype=np.intp)[:, None]
        x = x_order[(x_order[rows % width] + columns) % width]
        y = y_order[(y_order[x % height] + rows) % height] if two_axes else rows
        if inverse:
            result[y, x] = pixels[rows, columns]
        else:
            result[rows, columns] = pixels[y, x]
        _report(progress, min(height, begin + batch) / height, "正在置换二维像素" if two_axes else "正在置换行内像素")
    return result


def _block(pixels: np.ndarray, key: str, inverse: bool, progress: Callable | None) -> np.ndarray:
    """严格复现默认 32×32 分块及原项目的循环补边行为。"""
    height, width, channels = pixels.shape
    new_width, new_height = (width + 31) // 32 * 32, (height + 31) // 32 * 32
    block_width, block_height = new_width // 32, new_height // 32
    x_order, y_order = _md5_shuffle(32, key), _md5_shuffle(32, key)
    result = np.empty((new_height, new_width, channels), dtype=np.uint8)
    columns = np.arange(new_width, dtype=np.intp)[None, :]
    batch = max(1, min(128, 1_000_000 // new_width))
    for begin in range(0, new_height, batch):
        rows = np.arange(begin, min(new_height, begin + batch), dtype=np.intp)[:, None]
        x = (x_order[(rows // block_height) % 32] * block_width + columns) % new_width
        x = x_order[x // block_width] * block_width + x % block_width
        y = (y_order[(x // block_width) % 32] * block_height + rows) % new_height
        y = y_order[y // block_height] * block_height + y % block_height
        if inverse:
            # 外来 Block 图片尺寸不整除时仍按原 Java 的循环补边方式尝试。
            result[y, x] = pixels[rows % height, columns % width]
        else:
            result[rows, columns] = pixels[y % height, x % width]
        _report(progress, min(new_height, begin + batch) / new_height, "正在置换图块")
    return result


def _logistic_order(key: float, length: int) -> tuple[np.ndarray, float]:
    """用 IEEE 双精度按原顺序迭代 Logistic 序列，再按值取得排序索引。"""
    values = np.empty(length, dtype=np.float64)
    value = key
    values[0] = value
    for index in range(1, length):
        value = 3.9999999 * value * (1 - value)
        values[index] = value
    order = np.argsort(values, kind="stable")
    # 原 Java 比较器对相等值仍返回 -1，存在不稳定/异常行为，不能伪称通用兼容。
    if length > 1 and np.any(values[order[1:]] == values[order[:-1]]):
        raise ImageToolError("Logistic 序列出现重复值，原项目对此排序不稳定，请更换数值密钥。")
    return order, value


def _pic(pixels: np.ndarray, key: str, inverse: bool, two_axes: bool, progress: Callable | None) -> np.ndarray:
    """复现 PicEncrypt 单序列列置换，或每行、每列依次延续的序列置换。"""
    value = _numeric_key(key, logistic=True)
    height, width = pixels.shape[:2]
    if not two_axes:
        order, unused = _logistic_order(value, width)
        result = np.empty_like(pixels)
        if inverse:
            result[:, order] = pixels
        else:
            result[:] = pixels[:, order]
        return result
    result = pixels.copy()
    axes = (1, 0) if inverse else (0, 1)
    for phase, axis in enumerate(axes):
        source = result
        result = np.empty_like(source)
        current = value
        count, length = (height, width) if axis == 0 else (width, height)
        for index in range(count):
            order, current = _logistic_order(current, length)
            if axis == 0:
                if inverse:
                    result[index, order] = source[index]
                else:
                    result[index] = source[index, order]
            elif inverse:
                result[order, index] = source[:, index]
            else:
                result[:, index] = source[order, index]
            if index % 32 == 0 or index == count - 1:
                _report(progress, (phase + (index + 1) / count) / 2, "正在执行 PicEncrypt 行列置换")
    return result


def _argb(pixels: np.ndarray) -> np.ndarray:
    """将 RGB/RGBA 打包为 Java 使用的 32 位 ARGB，保留透明像素的 RGB。"""
    values = pixels.astype(np.uint32)
    alpha = values[:, :, 3] if values.shape[2] == 4 else np.full(values.shape[:2], 255, dtype=np.uint32)
    return (alpha << 24) | (values[:, :, 0] << 16) | (values[:, :, 1] << 8) | values[:, :, 2]


def _unpack_argb(values: np.ndarray) -> np.ndarray:
    """从 ARGB 解包 RGBA，确保原格式藏在 Alpha 中的列映射不丢失。"""
    return np.stack(((values >> 16) & 255, (values >> 8) & 255, values & 255, values >> 24), axis=2).astype(np.uint8)


def _column_order(pixels: np.ndarray, key: str, algorithm: str) -> np.ndarray:
    """生成原项目排序列或 Java 随机列的顺序。"""
    if algorithm == "random":
        return _random_order(pixels.shape[1], key)
    return np.argsort(_argb(pixels[:1])[0].view(np.int32), kind="stable")


def _validate_order(order: object, width: int) -> np.ndarray:
    """验证外部列映射为完整排列，防止越界、重复和畸形参数。"""
    if not isinstance(order, list) or len(order) != width:
        raise ImageToolError("列映射参数缺失或长度不正确。")
    if any(type(value) is not int or not 0 <= value < width for value in order):
        raise ImageToolError("列映射存在无效索引。")
    values = np.array(order, dtype=np.intp)
    if len(np.unique(values)) != width:
        raise ImageToolError("列映射存在重复，无法还原。")
    return values


def _legacy_columns(pixels: np.ndarray, key: str, algorithm: str, inverse: bool) -> np.ndarray:
    """精确兼容原项目末行嵌入映射及 RGB565 损失，不将结果标记为无损。"""
    height, width = pixels.shape[:2]
    if height < 2 and not inverse:
        raise ImageToolError("原项目 Sort/Random 格式至少需要两行像素。")
    packed = _argb(pixels)
    if inverse:
        order = _validate_order(((packed[-1] >> 16) & 65535).tolist(), width)
        restored = np.empty_like(packed)
        restored[:, order] = packed
        rgb565 = packed[-1] & 65535
        red = ((rgb565 >> 11) & 31) << 3
        green = ((rgb565 >> 5) & 63) << 2
        blue = (rgb565 & 31) << 3
        restored[-1, order] = np.uint32(0xFF000000) | (red << 16) | (green << 8) | blue
        return _unpack_argb(restored)
    order = _column_order(pixels, key, algorithm)
    result = packed.copy()
    result[:-1] = packed[:-1, order]
    # 为互通保留原实现的末行行为；无损模式不走此分支。
    red, green, blue = (packed[-1] >> 16) & 255, (packed[-1] >> 8) & 255, packed[-1] & 255
    rgb565 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    result[-1] = (order.astype(np.uint32) << 16) | rgb565
    return _unpack_argb(result)


def transform(
    image: Image.Image,
    key: str,
    algorithm: str,
    inverse: bool = False,
    compatible: bool = False,
    parameters: dict | None = None,
    progress: Callable | None = None,
) -> tuple[Image.Image, dict]:
    """分派八种算法，并明确分离原格式行为与本工具的无损适配。"""
    if algorithm not in ALGORITHM_IDS:
        raise ImageToolError("不支持此移植算法。")
    if not isinstance(key, str) or len(key.encode("utf-8")) > 1024:
        raise ImageToolError("密钥必须为 UTF-8 编码后不超过 1024 字节的字符串。")
    pixels = np.array(image, dtype=np.uint8, copy=True)
    parameters = parameters.copy() if parameters is not None else dict()
    if algorithm == "tomato":
        output = _tomato(pixels, key, inverse, progress)
    elif algorithm == "block":
        if not inverse and not compatible:
            parameters["original_size"] = list(image.size)
        output = _block(pixels, key, inverse, progress)
        if inverse and not compatible:
            size = parameters.get("original_size")
            if not isinstance(size, list) or len(size) != 2 or any(type(value) is not int or value < 1 for value in size):
                raise ImageToolError("Block 原图尺寸参数无效。")
            width, height = size
            if (width + 31) // 32 * 32 != image.width or (height + 31) // 32 * 32 != image.height:
                raise ImageToolError("Block 原图尺寸与补边尺寸不匹配。")
            output = output[:height, :width]
    elif algorithm in ("row-pixel", "per-pixel"):
        output = _pixel_transform(pixels, key, inverse, algorithm == "per-pixel", progress)
    elif algorithm in ("pic-row", "pic-row-column"):
        output = _pic(pixels, key, inverse, algorithm == "pic-row-column", progress)
    elif compatible:
        output = _legacy_columns(pixels, key, algorithm, inverse)
    elif inverse:
        if algorithm == "sort":
            order = _validate_order(parameters.get("column_order"), image.width)
        else:
            order = _random_order(image.width, key)
        output = np.empty_like(pixels)
        output[:, order] = pixels
    else:
        order = _column_order(pixels, key, algorithm)
        output = pixels[:, order]
        if algorithm == "sort":
            parameters["column_order"] = order.tolist()
    result = Image.fromarray(output)
    if isinstance(image.info.get("icc_profile"), bytes):
        result.info["icc_profile"] = image.info["icc_profile"]
    _report(progress, 1.0, "算法处理完成")
    return result, parameters
