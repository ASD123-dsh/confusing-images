"""核心回归测试：像素还原、参数校验、格式兼容与安全保存。"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from confusing_images import core


def make_image(width: int = 97, height: int = 73, mode: str = "RGB") -> Image.Image:
    """创建各像素不同的确定性测试图，透明通道也参与校验。"""
    channels = 4 if mode == "RGBA" else 3
    values = np.arange(width * height * channels, dtype=np.uint32)
    pixels = ((values * 37 + values // 251) % 256).astype(np.uint8)
    return Image.fromarray(pixels.reshape(height, width, channels))


class CoreTests(unittest.TestCase):
    """验证混淆流程不会损失边缘、透明度或输出像素。"""

    @classmethod
    def setUpClass(cls) -> None:
        """生成多个用例共用的混淆结果，减少重复的口令派生开销。"""
        cls.source = make_image()
        cls.password = "测试口令 Test-2026 !"
        cls.scrambled = core.scramble_image(cls.source, cls.password)

    def assert_same_pixels(self, left: Image.Image, right: Image.Image) -> None:
        """同时比较尺寸、模式及逐字节像素。"""
        self.assertEqual(left.size, right.size)
        self.assertEqual(left.mode, right.mode)
        self.assertEqual(left.tobytes(), right.tobytes())

    def test_roundtrip_odd_tiny_and_transparent_images(self) -> None:
        """验证奇数边缘、单行、单列、单像素及三档强度可逆。"""
        sizes = ((1, 1), (1, 17), (19, 1), (7, 5), (64, 48), (131, 109))
        for width, height in sizes:
            for mode in ("RGB", "RGBA"):
                for strength in (1, 2, 3):
                    with self.subTest(size=(width, height), mode=mode, strength=strength):
                        original = make_image(width, height, mode)
                        result = core.scramble_image(original, self.password, strength)
                        restored = core.restore_image(result, self.password)
                        self.assert_same_pixels(original, restored)
                        self.assertNotIn(core.METADATA_KEY, restored.info)

    def test_scrambling_changes_pixels_but_not_input(self) -> None:
        """确认混淆有效且不会原地修改输入图。"""
        before = self.source.tobytes()
        result = core.scramble_image(self.source, self.password)
        self.assertNotEqual(result.tobytes(), before)
        self.assertEqual(self.source.tobytes(), before)
        self.assertNotIn(core.METADATA_KEY, self.source.info)

    def test_salt_changes_result(self) -> None:
        """相同口令的不同随机盐应产生不同的混淆结果。"""
        first = core.scramble_image(self.source, self.password)
        second = core.scramble_image(self.source, self.password)
        self.assertNotEqual(first.tobytes(), second.tobytes())

    def test_fixed_salt_is_reproducible(self) -> None:
        """固定盐值时参数及置换完全确定，避免依赖运行时随机分布。"""
        with mock.patch.object(core.secrets, "token_bytes", return_value=bytes(range(16))):
            first = core.scramble_image(self.source, self.password)
            second = core.scramble_image(self.source, self.password)
        self.assertEqual(first.info[core.METADATA_KEY], second.info[core.METADATA_KEY])
        self.assert_same_pixels(first, second)

    def test_wrong_password_is_rejected(self) -> None:
        """错误口令必须明确报错，而不是返回看似成功的乱图。"""
        with self.assertRaisesRegex(core.ImageToolError, "口令不正确"):
            core.restore_image(self.scrambled, "错误口令")

    def test_v1_format_golden_vector(self) -> None:
        """锁定 v1 格式的像素和参数指纹，防止未来无意破坏旧图兼容性。"""
        with mock.patch.object(core.secrets, "token_bytes", return_value=bytes(range(16))):
            result = core.scramble_image(self.source, self.password)
        pixel_hash = hashlib.sha256(result.tobytes()).hexdigest()
        metadata_hash = hashlib.sha256(result.info[core.METADATA_KEY].encode("ascii")).hexdigest()
        self.assertEqual(pixel_hash, "870d094b11964087fedbb532f39b6bfa2b6124ae516e1311ddafb83121d18d3b")
        self.assertEqual(metadata_hash, "c22af0f9ab8c74a2de43121699732309b02e41c724d62f3e284cdcb723a514de")

    def test_password_whitespace_is_significant(self) -> None:
        """口令前后空格不被静默删除。"""
        scrambled = core.scramble_image(self.source, " password ")
        self.assert_same_pixels(self.source, core.restore_image(scrambled, " password "))
        with self.assertRaises(core.ImageToolError):
            core.restore_image(scrambled, "password")

    def test_invalid_and_oversized_passwords_are_rejected(self) -> None:
        """空字符串现为无口令模式，仍拒绝非字符串和超长口令。"""
        for password in (None, 123, "密" * 400):
            with self.subTest(password_type=type(password).__name__):
                with self.assertRaises(core.ImageToolError):
                    core.scramble_image(self.source, password)

    def test_invalid_strength_is_rejected(self) -> None:
        """布尔、字符串和越界强度不能进入算法。"""
        for strength in (0, 4, True, "2"):
            with self.subTest(strength=strength):
                with self.assertRaises(core.ImageToolError):
                    core.scramble_image(self.source, self.password, strength)

    def test_pixel_tampering_is_rejected(self) -> None:
        """单个像素被修改也必须触发完整性校验。"""
        changed = self.scrambled.copy()
        pixel = changed.getpixel((0, 0))
        changed.putpixel((0, 0), ((pixel[0] + 1) % 256, pixel[1], pixel[2]))
        with self.assertRaisesRegex(core.ImageToolError, "已改变"):
            core.restore_image(changed, self.password)

    def test_metadata_tampering_is_rejected(self) -> None:
        """修改合法范围内的强度参数也必须被认证校验发现。"""
        changed = self.scrambled.copy()
        metadata = json.loads(changed.info[core.METADATA_KEY])
        metadata["strength"] = 3
        changed.info[core.METADATA_KEY] = json.dumps(metadata)
        with self.assertRaisesRegex(core.ImageToolError, "已改变"):
            core.restore_image(changed, self.password)

    def test_missing_metadata_is_rejected(self) -> None:
        """普通图片不能被误当作本工具的混淆图。"""
        with self.assertRaisesRegex(core.ImageToolError, "还原参数已丢失"):
            core.restore_image(self.source, self.password)

    def test_malformed_metadata_is_rejected(self) -> None:
        """损坏 JSON、错误字段和不兼容版本均应安全拒绝。"""
        valid = json.loads(self.scrambled.info[core.METADATA_KEY])
        changes = (
            dict(version=2), dict(version=True), dict(algorithm="unknown"),
            dict(strength=True), dict(strength=9), dict(width="97"),
            dict(salt="z" * 32), dict(password_tag="0"),
            dict(extra="unexpected"), dict(mode="RGBA"),
        )
        encoded_values = ["{", "[]", "null", "x" * (core.MAX_METADATA_LENGTH + 1)]
        for change in changes:
            metadata = valid.copy()
            metadata.update(change)
            encoded_values.append(json.dumps(metadata))
        incomplete = valid.copy()
        incomplete.pop("salt")
        encoded_values.append(json.dumps(incomplete))
        for encoded in encoded_values:
            with self.subTest(encoded=encoded[:80]):
                changed = self.scrambled.copy()
                changed.info[core.METADATA_KEY] = encoded
                with self.assertRaises(core.ImageToolError):
                    core.restore_image(changed, self.password)

    def test_resizing_is_rejected(self) -> None:
        """图片尺寸变化后不可继续执行逆置换。"""
        changed = self.scrambled.resize((50, 50))
        with self.assertRaisesRegex(core.ImageToolError, "尺寸或颜色模式"):
            core.restore_image(changed, self.password)

    def test_png_disk_roundtrip_with_unicode_path(self) -> None:
        """真实写入并读取中文路径，确认 PNG 保留全部还原参数。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中文路径 混淆图.png"
            core.save_png(self.scrambled, path)
            loaded = core.load_image(path)
            restored = core.restore_image(loaded, self.password)
            self.assert_same_pixels(self.source, restored)
            output = Path(directory) / "还原图.png"
            core.save_png(restored, output)
            with Image.open(output) as saved:
                self.assertNotIn(core.METADATA_KEY, saved.info)
                self.assert_same_pixels(self.source, saved)

    def test_rgba_disk_roundtrip(self) -> None:
        """透明 PNG 的 Alpha 像素经磁盘保存后依然完全一致。"""
        original = make_image(53, 47, "RGBA")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alpha.png"
            core.save_png(core.scramble_image(original, self.password), path)
            self.assert_same_pixels(original, core.restore_image(core.load_image(path), self.password))

    def test_input_formats_and_modes(self) -> None:
        """灰度、灰度透明、调色板透明及常见静态格式正确归一化。"""
        palette = Image.new("P", (30, 20), 0)
        palette.putpalette([value for index in range(256) for value in (index, 255 - index, index)])
        palette.info["transparency"] = 0
        images = (
            (Image.new("L", (30, 20), 127), "PNG", "RGB"),
            (Image.new("LA", (30, 20), (127, 80)), "PNG", "RGBA"),
            (palette, "PNG", "RGBA"),
            (self.source, "JPEG", "RGB"),
            (self.source, "BMP", "RGB"),
            (self.source, "WEBP", "RGB"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (image, format_name, mode) in enumerate(images):
                with self.subTest(format=format_name, mode=image.mode):
                    path = Path(directory) / f"image-{index}.dat"
                    image.save(path, format=format_name)
                    loaded = core.load_image(path)
                    self.assertEqual(loaded.mode, mode)
                    self.assertEqual(loaded.size, image.size)

    def test_exif_orientation_and_privacy(self) -> None:
        """JPEG 方向应先校正，导出不携带源图 EXIF 信息。"""
        with tempfile.TemporaryDirectory() as directory:
            exif = Image.Exif()
            exif[274] = 6
            exif[315] = "private author"
            path = Path(directory) / "oriented.jpg"
            self.source.save(path, exif=exif)
            loaded = core.load_image(path)
            self.assertEqual(loaded.size, (self.source.height, self.source.width))
            self.assertNotIn("exif", loaded.info)
            mixed = core.scramble_image(loaded, self.password)
            output = Path(directory) / "mixed.png"
            core.save_png(mixed, output)
            with Image.open(output) as saved:
                self.assertFalse(saved.getexif())
            self.assert_same_pixels(loaded, core.restore_image(core.load_image(output), self.password))

    def test_color_profile_is_preserved(self) -> None:
        """在混淆、还原和保存中保留 ICC 字节，避免丢失颜色配置。"""
        original = self.source.copy()
        original.info["icc_profile"] = b"test-profile-bytes"
        mixed = core.scramble_image(original, self.password)
        restored = core.restore_image(mixed, self.password)
        self.assertEqual(restored.info["icc_profile"], original.info["icc_profile"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.png"
            core.save_png(mixed, path)
            self.assertEqual(core.load_image(path).info["icc_profile"], original.info["icc_profile"])

    def test_broken_and_missing_images(self) -> None:
        """损坏和缺失文件给出可读错误，不再按固定扩展名白名单拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.png"
            path.write_bytes(b"not an image")
            with self.assertRaises(core.ImageToolError):
                core.load_image(path)
            with self.assertRaises(core.ImageToolError):
                core.load_image(Path(directory) / "missing.png")

    def test_gif_and_unknown_extension_are_decoded_by_content(self) -> None:
        """静态 GIF 及无扩展名文件按图片内容识别，不再受来源或后缀限制。"""
        with tempfile.TemporaryDirectory() as directory:
            gif = Path(directory) / "static.gif"
            self.source.save(gif, format="GIF")
            loaded = core.load_image(gif)
            self.assertEqual(loaded.size, self.source.size)
            without_extension = Path(directory) / "no-extension"
            self.source.save(without_extension, format="PNG")
            self.assertEqual(core.load_image(without_extension).size, self.source.size)

    def test_animated_image_is_rejected(self) -> None:
        """不能静默丢弃动画的其余帧，改为提示先导出单帧。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animated.webp"
            second_frame = Image.new("RGB", self.source.size, "#FFAA00")
            self.source.save(path, save_all=True, append_images=[second_frame])
            with self.assertRaisesRegex(core.ImageToolError, "动画或多页"):
                core.load_image(path)

    def test_no_fixed_pixel_limit(self) -> None:
        """不再设置固定像素或边长上限，由实际可用内存决定能否处理。"""
        source = self.source.copy()
        # 不依赖原先的 MAX_PIXELS/MAX_DIMENSION 常量，确认没有该限制即可正常归一化。
        normalized = core._normalize_image(source)
        self.assertEqual(normalized.size, source.size)
        self.assertFalse(hasattr(core, "MAX_PIXELS"))
        self.assertFalse(hasattr(core, "MAX_DIMENSION"))

    def test_chain_scramble_and_layer_by_layer_restore(self) -> None:
        """多次混淆保存上一层参数，点一次还原一层，逐层回到原图。"""
        original = self.source
        layered = original
        for round_index in range(3):
            layered = core.scramble_image(layered, self.password, algorithm="tomato")
        current = layered
        for round_index in range(3):
            current = core.restore_image(current, self.password)
            # 还原过程中除最后一层外，结果仍带参数；最后一层应回到无参数原图。
            if round_index < 2:
                self.assertIn(core.METADATA_KEY, current.info)
            else:
                self.assertNotIn(core.METADATA_KEY, current.info)
        self.assertEqual(current.tobytes(), original.tobytes())

    def test_chain_mixed_algorithms_restore(self) -> None:
        """不同算法逐层叠加后，仍可逐层正确还原。"""
        original = self.source
        layered = core.scramble_image(original, self.password, algorithm="block")
        layered = core.scramble_image(layered, self.password, algorithm="per-pixel")
        layered = core.scramble_image(layered, self.password, algorithm="tomato")
        current = core.restore_image(layered, self.password)
        self.assertEqual(core.image_algorithm(current), "per-pixel")
        current = core.restore_image(current, self.password)
        self.assertEqual(core.image_algorithm(current), "block")
        current = core.restore_image(current, self.password)
        self.assertEqual(current.tobytes(), original.tobytes())

    def test_non_png_save_is_rejected(self) -> None:
        """禁止用看似支持的扩展名输出有损格式。"""
        with self.assertRaisesRegex(core.ImageToolError, "只能保存"):
            core.save_png(self.scrambled, "output.jpg")

    def test_failed_save_preserves_existing_file(self) -> None:
        """模拟写盘失败，已有目标文件和临时文件均应得到正确处理。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.png"
            path.write_bytes(b"original content")
            with mock.patch.object(Image.Image, "save", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(core.ImageToolError, "保存失败"):
                    core.save_png(self.scrambled, path)
            self.assertEqual(path.read_bytes(), b"original content")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_progress_is_ordered(self) -> None:
        """处理进度必须从头到尾单调前进并报告完成。"""
        calls = []
        core.scramble_image(self.source, self.password, progress=lambda value, text: calls.append((value, text)))
        values = [value for value, text in calls]
        self.assertEqual(sorted(values), values)
        self.assertEqual(values[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
