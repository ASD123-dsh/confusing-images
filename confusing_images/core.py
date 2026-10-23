"""图片处理核心：多尺度块置换、PNG 参数封装与像素完整性校验。

此算法用于视觉混淆，不提供现代加密算法的保密性保证。
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from typing import Callable

import numpy as np
from PIL import Image, ImageOps, PngImagePlugin, UnidentifiedImageError

from .errors import ImageToolError
from . import upstream

METADATA_KEY = "confusing-images"
ALGORITHM = "multiscale-blocks-v1"
FORMAT_VERSION = 1
KDF_ITERATIONS = 200_000
MAX_METADATA_LENGTH = 524_288
RESTORE_STATUS = "_confusing_images_restore_status"
RESTORE_NOTE = "_confusing_images_restore_note"
_IMAGE_IO_LOCK = threading.RLock()
ProgressCallback = Callable[[float, str], None]


def _report(progress: ProgressCallback | None, value: float, text: str) -> None:
    """通过可选回调报告进度，不依赖图形界面。"""
    if progress is not None:
        progress(value, text)


def _validate_size(width: int, height: int) -> None:
    """只检查尺寸是否合法，不设置固定像素数或边长上限。"""
    if width < 1 or height < 1:
        raise ImageToolError("图片尺寸无效。")


@contextmanager
def _open_image(path: str | Path):
    """读取时临时关闭 Pillow 固定像素阈值，完成或异常后恢复其全局配置。"""
    with _IMAGE_IO_LOCK:
        previous = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as source:
                yield source
        finally:
            Image.MAX_IMAGE_PIXELS = previous


@lru_cache(maxsize=1)
def supported_extensions() -> tuple[str, ...]:
    """从实际安装的 Pillow 读取解码扩展名，不维护人为格式白名单。"""
    Image.init()
    return tuple(sorted(extension.lower() for extension, format_name in Image.registered_extensions().items() if format_name in Image.OPEN))


def is_image_candidate(path: str | Path) -> bool:
    """已知扩展名保留以便报告损坏；未知扩展名通过图片头识别而非直接排除。"""
    path = Path(path)
    if path.suffix.lower() in supported_extensions():
        return True
    try:
        with _open_image(path) as source:
            _validate_size(*source.size)
            return True
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return False


def _normalize_image(image: Image.Image) -> Image.Image:
    """统一为经方向校正的八位 RGB/RGBA，并仅保留颜色配置和本工具参数。"""
    _validate_size(*image.size)
    metadata = image.info.get(METADATA_KEY)
    profile = image.info.get("icc_profile")
    corrected = ImageOps.exif_transpose(image)
    has_alpha = corrected.mode in ("RGBA", "LA", "PA")
    has_alpha = has_alpha or "transparency" in corrected.info
    normalized = corrected.convert("RGBA" if has_alpha else "RGB")
    normalized.info.clear()
    if isinstance(profile, bytes):
        normalized.info["icc_profile"] = profile
    if isinstance(metadata, str):
        normalized.info[METADATA_KEY] = metadata
    return normalized


def load_image(path: str | Path) -> Image.Image:
    """按实际内容读取 Pillow 可解码的静态图片，不限制来源、后缀或固定尺寸。"""
    try:
        with _open_image(path) as source:
            _validate_size(*source.size)
            if getattr(source, "n_frames", 1) != 1:
                raise ImageToolError("当前处理静态图片，动画或多页文件请先导出所需单帧。")
            source.load()
            return _normalize_image(source)
    except ImageToolError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageToolError("解码器无法读取图片尺寸，请检查文件是否完整。") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ImageToolError("解码器无法读取此文件，请检查文件是否完整或安装的图片解码器是否支持。") from error


def _derive_keys(password: str, salt: bytes) -> tuple[bytes, bytes]:
    """从原样输入的口令派生置换与校验密钥，不保存口令。"""
    if not isinstance(password, str) or not password:
        raise ImageToolError("请输入口令；还原时必须输入完全相同的口令。")
    encoded = password.encode("utf-8")
    if len(encoded) > 1_024:
        raise ImageToolError("口令过长，UTF-8 编码后最多支持 1024 字节。")
    derived = hashlib.pbkdf2_hmac("sha256", encoded, salt, KDF_ITERATIONS, dklen=64)
    return derived[:32], derived[32:]


def _public_keys(salt: bytes) -> tuple[bytes, bytes]:
    """从公开随机盐派生无口令参数，仅供可逆置换和损坏检查，不提供保密性。"""
    derived = hashlib.shake_256(b"confusing-images/public-parameters/v1\x00" + salt).digest(64)
    return derived[:32], derived[32:]


def _canonical_json(metadata: dict) -> str:
    """用稳定的键顺序和分隔符编码参数，确保不同环境校验一致。"""
    return json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _image_tag(image: Image.Image, key: bytes, header: str = "") -> str:
    """对模式、尺寸、参数及完整像素计算带密钥的校验值。"""
    digest = hmac.new(key, digestmod="sha256")
    description = f"{image.mode}:{image.width}:{image.height}:".encode("ascii")
    digest.update(description)
    digest.update(header.encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def _password_tag(key: bytes) -> str:
    """生成口令校验标记，便于在执行逆置换前提示错误口令。"""
    return hmac.new(key, b"confusing-images/password/v1", "sha256").hexdigest()


def _scales(width: int, height: int, strength: int) -> list[tuple[int, int]]:
    """生成由粗到细的块尺寸，保留大色块与细碎纹理相间的效果。"""
    target = max(1, min(width, height) // 4)
    block = min(256, 1 << (target.bit_length() - 1))
    minimum = (16, 4, 2)[strength - 1]
    levels = [(block, 256)]
    block //= 2
    while block >= minimum:
        threshold = (160, 192, 224)[strength - 1]
        levels.append((block, threshold))
        block //= 2
    return levels


def _select_tiles(
    scores: np.ndarray,
    rows: int,
    columns: int,
    key: bytes,
    label: str,
    threshold: int,
) -> np.ndarray:
    """通过相关的层级掩码选择细块，形成成片细碎而非均匀撒点的效果。"""
    selected = ((scores & np.uint64(255)) < threshold).reshape(rows, columns)
    level_text, region = label.split(":")
    level = int(level_text)
    # 修复独立小块选取产生均匀椒盐纹理的问题：共同祖先未选中时保留整片大块。
    for ancestor in range(level):
        factor = 1 << (level - ancestor)
        gate_rows = (rows + factor - 1) // factor
        gate_columns = (columns + factor - 1) // factor
        domain = f"gate:{region}:{ancestor}".encode("ascii")
        stream = hashlib.shake_256(key + domain).digest(gate_rows * gate_columns)
        gates = np.frombuffer(stream, dtype=np.uint8).reshape(gate_rows, gate_columns)
        allowed = gates < (threshold - 16)
        expanded = allowed.repeat(factor, axis=0).repeat(factor, axis=1)
        selected &= expanded[:rows, :columns]
    return np.flatnonzero(selected)


def _permute_region(
    pixels: np.ndarray,
    top: int,
    left: int,
    rows: int,
    columns: int,
    block_height: int,
    block_width: int,
    key: bytes,
    label: str,
    threshold: int,
    inverse: bool,
) -> None:
    """在同形状块之间执行可逆置换，边缘区域独立处理以避免丢失像素。"""
    count = rows * columns
    if count < 2:
        return
    # 使用标准 SHAKE 字节流，而非依赖第三方随机分布函数的版本行为。
    stream = hashlib.shake_256(key + label.encode("ascii")).digest(count * 8)
    scores = np.frombuffer(stream, dtype="<u8")
    selected = _select_tiles(scores, rows, columns, key, label, threshold)
    if len(selected) < 2:
        return
    order = np.argsort(scores[selected], kind="stable")
    height = rows * block_height
    width = columns * block_width
    channels = pixels.shape[2]
    region = pixels[top:top + height, left:left + width]
    tiles = region.reshape(rows, block_height, columns, block_width, channels)
    tiles = tiles.transpose(0, 2, 1, 3, 4).reshape(count, block_height, block_width, channels).copy()
    if inverse:
        tiles[selected[order]] = tiles[selected]
    else:
        tiles[selected] = tiles[selected[order]]
    rebuilt = tiles.reshape(rows, columns, block_height, block_width, channels)
    region[:] = rebuilt.transpose(0, 2, 1, 3, 4).reshape(height, width, channels)


def _apply_level(
    pixels: np.ndarray,
    block: int,
    threshold: int,
    key: bytes,
    level: int,
    inverse: bool,
) -> None:
    """分别处理完整块、右边条及底边条，支持任意长宽和透明通道。"""
    height, width = pixels.shape[:2]
    block_height, block_width = min(block, height), min(block, width)
    rows, remainder_height = divmod(height, block_height)
    columns, remainder_width = divmod(width, block_width)
    _permute_region(
        pixels, 0, 0, rows, columns, block_height, block_width,
        key, f"{level}:body", threshold, inverse,
    )
    if remainder_width:
        _permute_region(
            pixels, 0, columns * block_width, rows, 1, block_height, remainder_width,
            key, f"{level}:right", threshold, inverse,
        )
    if remainder_height:
        _permute_region(
            pixels, rows * block_height, 0, 1, columns, remainder_height, block_width,
            key, f"{level}:bottom", threshold, inverse,
        )


def _transform(
    image: Image.Image,
    key: bytes,
    strength: int,
    inverse: bool,
    progress: ProgressCallback | None,
) -> Image.Image:
    """依次应用或逆序撤销各尺度置换，不修改输入图片。"""
    pixels = np.array(image, dtype=np.uint8, copy=True)
    levels = list(enumerate(_scales(image.width, image.height, strength)))
    if inverse:
        levels.reverse()
    for step, (level, (block, threshold)) in enumerate(levels):
        verb = "还原" if inverse else "混淆"
        _report(progress, 0.15 + 0.7 * step / len(levels), f"正在{verb}第 {step + 1}/{len(levels)} 层")
        _apply_level(pixels, block, threshold, key, level, inverse)
    result = Image.fromarray(pixels)
    profile = image.info.get("icc_profile")
    if isinstance(profile, bytes):
        result.info["icc_profile"] = profile
    return result


def _parse_metadata(image: Image.Image) -> dict:
    """严格检查 PNG 参数，拒绝缺失、损坏和不兼容版本的数据。"""
    encoded = image.info.get(METADATA_KEY)
    if not isinstance(encoded, str):
        raise ImageToolError("这不是本工具生成的混淆图，或 PNG 还原参数已丢失。")
    if len(encoded) > max(MAX_METADATA_LENGTH, image.width * 12 + 8192):
        raise ImageToolError("图片的还原参数过大或已损坏。")
    try:
        metadata = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise ImageToolError("图片的还原参数已损坏。") from error
    if not isinstance(metadata, dict):
        raise ImageToolError("图片的还原参数格式不正确。")
    version = metadata.get("version")
    if type(version) is not int or version not in (1, 2, 3):
        raise ImageToolError("不支持此混淆图版本，请使用对应版本的工具还原。")
    algorithm = metadata.get("algorithm")
    supported = algorithm == ALGORITHM if version == 1 else algorithm in upstream.ALGORITHM_IDS
    if version == 3:
        supported = algorithm == ALGORITHM or algorithm in upstream.ALGORITHM_IDS
    if not supported:
        raise ImageToolError("不支持此混淆图算法。")
    expected_fields = (
        "version", "algorithm", "width", "height", "mode", "strength",
        "salt", "password_tag", "original_tag", "payload_tag",
    )
    if version in (2, 3):
        expected_fields += ("parameters",)
    if version == 3:
        expected_fields += ("key_mode",)
    required = set(expected_fields)
    allowed = required | {"upper_metadata"}
    if not required <= set(metadata) <= allowed:
        raise ImageToolError("图片的还原参数不完整或已损坏。")
    if version in (2, 3) and not isinstance(metadata["parameters"], dict):
        raise ImageToolError("图片的算法参数格式不正确。")
    if version == 3 and metadata["key_mode"] != "none":
        raise ImageToolError("图片的无口令模式参数无效。")
    upper = metadata.get("upper_metadata")
    if upper is not None:
        # 链式多次混淆：上一层参数必须是可解析的 JSON 对象，否则拒绝继续回退。
        if not isinstance(upper, str) or not upper or len(upper) > MAX_METADATA_LENGTH:
            raise ImageToolError("图片的上一层参数无效或过大。")
        try:
            previous = json.loads(upper)
        except (ValueError, RecursionError) as error:
            raise ImageToolError("图片的上一层参数已损坏。") from error
        if not isinstance(previous, dict):
            raise ImageToolError("图片的上一层参数格式不正确。")
    for name in ("width", "height", "strength"):
        if type(metadata[name]) is not int:
            raise ImageToolError("图片的尺寸或强度参数无效。")
    if metadata["strength"] not in (1, 2, 3):
        raise ImageToolError("图片的混淆强度参数无效。")
    if (metadata["width"], metadata["height"]) != image.size or metadata["mode"] != image.mode:
        raise ImageToolError("图片尺寸或颜色模式已改变，无法无损还原。")
    for name, length in (("salt", 32), ("password_tag", 64), ("original_tag", 64), ("payload_tag", 64)):
        value = metadata[name]
        if not isinstance(value, str) or len(value) != length:
            raise ImageToolError("图片的校验参数已损坏。")
        if any(character not in "0123456789abcdef" for character in value):
            raise ImageToolError("图片的校验参数格式不正确。")
    return metadata


def scramble_image(
    image: Image.Image,
    password: str = "",
    strength: int = 2,
    progress: ProgressCallback | None = None,
    *,
    algorithm: str = ALGORITHM,
) -> Image.Image:
    """生成 PNG：有口令保持 v1/v2，无口令使用公开参数的 v3 格式。"""
    if type(strength) is not int or strength not in (1, 2, 3):
        raise ImageToolError("混淆强度必须为 1、2 或 3。")
    if algorithm != ALGORITHM and algorithm not in upstream.ALGORITHM_IDS:
        raise ImageToolError("不支持此混淆算法。")
    if not isinstance(password, str):
        raise ImageToolError("口令必须是字符串，可以留空。")
    source = _normalize_image(image)
    _report(progress, 0.02, "正在派生口令参数" if password else "正在生成无口令公开参数")
    salt = secrets.token_bytes(16)
    shuffle_key, auth_key = _derive_keys(password, salt) if password else _public_keys(salt)
    parameters = dict()
    if algorithm == ALGORITHM:
        result = _transform(source, shuffle_key, strength, False, progress)
    else:
        result, parameters = upstream.transform(
            source, upstream.native_key(algorithm, shuffle_key), algorithm,
            progress=lambda value, text: _report(progress, 0.15 + 0.7 * value, text),
        )
    _report(progress, 0.9, "正在生成像素校验与还原参数")
    metadata = dict(
        version=(FORMAT_VERSION if algorithm == ALGORITHM else 2) if password else 3,
        algorithm=algorithm,
        width=result.width,
        height=result.height,
        mode=source.mode,
        strength=strength,
        salt=salt.hex(),
        password_tag=_password_tag(auth_key),
        original_tag=_image_tag(source, auth_key),
    )
    if algorithm != ALGORITHM or not password:
        metadata["parameters"] = parameters
    if not password:
        metadata["key_mode"] = "none"
    # 链式多次混淆：把输入自带的上一层参数保存下来，参与本次完整性认证。
    previous_metadata = source.info.get(METADATA_KEY)
    if isinstance(previous_metadata, str):
        metadata["upper_metadata"] = previous_metadata
    metadata["payload_tag"] = _image_tag(result, auth_key, _canonical_json(metadata))
    result.info[METADATA_KEY] = _canonical_json(metadata)
    _report(progress, 1.0, "混淆完成，请保存为 PNG")
    return result


def restore_image(
    image: Image.Image,
    password: str = "",
    progress: ProgressCallback | None = None,
) -> Image.Image:
    """自动识别是否需要口令，检查混淆图完整性并校验还原像素。"""
    _validate_size(*image.size)
    metadata = _parse_metadata(image)
    public = metadata["version"] == 3
    _report(progress, 0.02, "正在读取无口令参数" if public else "正在校验口令")
    salt = bytes.fromhex(metadata["salt"])
    if public:
        # 无口令 PNG 不使用输入框中残留的其他口令，允许与有口令图片混合批量还原。
        shuffle_key, auth_key = _public_keys(salt)
    else:
        if not password:
            raise ImageToolError("这张图片设有口令，请输入生成时使用的口令。")
        shuffle_key, auth_key = _derive_keys(password, salt)
    if not hmac.compare_digest(metadata["password_tag"], _password_tag(auth_key)):
        if public:
            raise ImageToolError("图片的无口令校验参数已损坏。")
        raise ImageToolError("口令不正确，或图片中的口令校验参数已损坏。")
    header = metadata.copy()
    expected_tag = header.pop("payload_tag")
    actual_tag = _image_tag(image, auth_key, _canonical_json(header))
    if not hmac.compare_digest(expected_tag, actual_tag):
        raise ImageToolError("图片或还原参数已改变，可能经过压缩、编辑或损坏，无法无损还原。")
    algorithm = metadata["algorithm"]
    if algorithm == ALGORITHM:
        result = _transform(image, shuffle_key, metadata["strength"], True, progress)
    else:
        result, unused = upstream.transform(
            image, upstream.native_key(algorithm, shuffle_key), algorithm,
            inverse=True, parameters=metadata["parameters"],
            progress=lambda value, text: _report(progress, 0.15 + 0.7 * value, text),
        )
    _report(progress, 0.9, "正在验证还原像素")
    if not hmac.compare_digest(metadata["original_tag"], _image_tag(result, auth_key)):
        raise ImageToolError("还原像素校验失败，请使用兼容版本的工具和未修改的 PNG。")
    # 链式多次混淆：还原一层后把上一层参数写回，结果仍是可继续逐层还原的带参 PNG。
    upper = metadata.get("upper_metadata")
    if upper is not None:
        result.info[METADATA_KEY] = upper
    _report(progress, 1.0, "已还原一层，像素校验通过")
    return result


def image_algorithm(image: Image.Image) -> str:
    """读取并验证本工具图片的算法标识，供界面自动选择而无需知道口令。"""
    return _parse_metadata(image)["algorithm"]


def image_requires_password(image: Image.Image) -> bool:
    """读取图片的口令要求；旧版 v1/v2 始终需要原口令。"""
    return _parse_metadata(image)["version"] != 3


def process_compatible(
    image: Image.Image,
    key: str,
    algorithm: str,
    inverse: bool = False,
    progress: ProgressCallback | None = None,
) -> Image.Image:
    """处理原项目无参数图片，不添加本工具元数据，也不声称验证了口令。"""
    source = _normalize_image(image)
    result, unused = upstream.transform(source, key, algorithm, inverse=inverse, compatible=True, progress=progress)
    return result


def manual_restore_description(algorithm: str, key: str = "") -> str:
    """说明手动尝试的实际参数规则，不在状态或日志中显示用户填写的密钥。"""
    effective = upstream.manual_restore_key(algorithm, key)
    if algorithm in ("sort", "random"):
        return "原格式还原依赖末行完整列映射，不验证密钥；结果未校验。"
    if key:
        return "按所选算法和输入密钥尝试；需匹配原图参数，结果未校验。"
    if algorithm in upstream.NUMERIC_ALGORITHMS:
        return f"空密钥将使用默认参数 {effective} 尝试；不代表原图使用该参数，结果未校验。"
    return "空密钥将使用空字符串作为原项目密钥尝试；结果未校验。"


def restore_selected_image(
    image: Image.Image,
    key: str = "",
    *,
    algorithm: str = "tomato",
    manual: bool = False,
    progress: ProgressCallback | None = None,
) -> Image.Image:
    """有元数据自动严格还原，无元数据或用户强制手动时按选择尝试，不限制图片来源。"""
    if METADATA_KEY in image.info and not manual:
        result = restore_image(image, key, progress)
        result.info[RESTORE_STATUS] = "verified"
        result.info[RESTORE_NOTE] = "像素校验通过"
        return result
    effective = upstream.manual_restore_key(algorithm, key)
    result = process_compatible(image, effective, algorithm, True, progress)
    result.info[RESTORE_STATUS] = "unverified"
    result.info[RESTORE_NOTE] = manual_restore_description(algorithm, key)
    _report(progress, 1.0, "已生成手动尝试结果，未验证原图或密钥")
    return result


def save_png(image: Image.Image, path: str | Path, *, overwrite: bool = True) -> Path:
    """原子保存 PNG；批量模式可禁止覆盖，并用 FileExistsError 报告重名。"""
    output = Path(path)
    if output.suffix.lower() != ".png":
        raise ImageToolError("结果只能保存为 .png，JPEG 等有损格式会破坏还原条件。")
    metadata = image.info.get(METADATA_KEY)
    png_info = PngImagePlugin.PngInfo()
    if metadata is not None:
        _parse_metadata(image)
        png_info.add_itxt(METADATA_KEY, metadata)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".confusing-images-", suffix=".png", dir=output.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        image.save(
            temporary_path,
            format="PNG",
            pnginfo=png_info,
            icc_profile=image.info.get("icc_profile"),
        )
        if overwrite:
            os.replace(temporary_path, output)
        elif os.name == "nt":
            # 批量重名保护：Windows rename 在目标已存在时失败，不覆盖竞争创建的文件。
            os.rename(temporary_path, output)
        else:
            # 非 Windows 的同目录硬链接也提供原子且禁止覆盖的提交方式。
            os.link(temporary_path, output)
        return output
    except FileExistsError as error:
        if not overwrite:
            raise
        raise ImageToolError("保存失败，目标路径已被其他文件或目录占用。") from error
    except OSError as error:
        raise ImageToolError("保存失败，请检查目录是否存在、文件是否被占用以及写入权限。") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
