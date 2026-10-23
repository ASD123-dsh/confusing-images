"""可选口令回归：无口令 v3、旧图保护、完整性及混合批量还原。"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from confusing_images import __third_party_notice__, batch, core, upstream


def make_image(mode: str = "RGBA") -> Image.Image:
    """生成含色块及透明度的小图，验证全部像素可逆。"""
    image = Image.new("RGBA", (39, 27), (37, 149, 207, 73))
    image.paste((224, 83, 51, 159), (3, 4, 24, 19))
    return image if mode == "RGBA" else image.convert(mode)


class OptionalPasswordTests(unittest.TestCase):
    """验证留空是公开参数模式，而不是绕过旧图口令检查。"""

    def test_all_eight_algorithms_without_password(self) -> None:
        """八种算法在 RGB/RGBA 下均可直接混淆并无口令还原。"""
        for algorithm in upstream.ALGORITHM_IDS:
            for mode in ("RGB", "RGBA"):
                with self.subTest(algorithm=algorithm, mode=mode):
                    image = make_image(mode)
                    mixed = core.scramble_image(image, algorithm=algorithm)
                    metadata = json.loads(mixed.info[core.METADATA_KEY])
                    self.assertEqual((metadata["version"], metadata["key_mode"]), (3, "none"))
                    self.assertFalse(core.image_requires_password(mixed))
                    restored = core.restore_image(mixed)
                    self.assertEqual((restored.size, restored.mode), (image.size, image.mode))
                    self.assertEqual(restored.tobytes(), image.tobytes())

    def test_public_png_disk_roundtrip(self) -> None:
        """无口令模式参数随 PNG 保存，重新读取后仍可还原。"""
        original = make_image()
        with tempfile.TemporaryDirectory() as directory:
            for algorithm in upstream.ALGORITHM_IDS:
                path = Path(directory) / (algorithm + ".png")
                core.save_png(core.scramble_image(original, algorithm=algorithm), path)
                restored = core.restore_image(core.load_image(path))
                self.assertEqual(restored.tobytes(), original.tobytes())

    def test_v3_public_golden_vector(self) -> None:
        """固定公开参数格式指纹，避免后续修改使已生成的无口令图片无法还原。"""
        with mock.patch.object(core.secrets, "token_bytes", return_value=bytes(range(16))):
            mixed = core.scramble_image(make_image(), algorithm="tomato")
        pixels_hash = hashlib.sha256(mixed.tobytes()).hexdigest()
        metadata_hash = hashlib.sha256(mixed.info[core.METADATA_KEY].encode("ascii")).hexdigest()
        self.assertEqual(pixels_hash, "5d6c64d1e1e6816587216e4f24d15f636df8ebcb29340ba78e2ee69fd7f485ac")
        self.assertEqual(metadata_hash, "76221650991ace7668b5ca34c5293ecc55e19963d762b8a4409a2559352e8969")

    def test_public_image_ignores_leftover_password(self) -> None:
        """无口令图片不会误用输入框里其他图片的口令。"""
        image = make_image()
        mixed = core.scramble_image(image, algorithm="tomato")
        restored = core.restore_image(mixed, "输入框残留的其他口令")
        self.assertEqual(restored.tobytes(), image.tobytes())

    def test_password_protected_v1_v2_still_require_original_password(self) -> None:
        """旧版口令格式不能靠留空或新无口令分支绕过。"""
        image = make_image()
        for algorithm in (core.ALGORITHM, "tomato"):
            with self.subTest(algorithm=algorithm):
                mixed = core.scramble_image(image, "旧版原口令", algorithm=algorithm)
                self.assertTrue(core.image_requires_password(mixed))
                self.assertIn(json.loads(mixed.info[core.METADATA_KEY])["version"], (1, 2))
                with self.assertRaisesRegex(core.ImageToolError, "设有口令"):
                    core.restore_image(mixed)
                with self.assertRaisesRegex(core.ImageToolError, "口令不正确"):
                    core.restore_image(mixed, "wrong")
                self.assertEqual(core.restore_image(mixed, "旧版原口令").tobytes(), image.tobytes())

    def test_space_is_a_password_not_empty_mode(self) -> None:
        """不自动删除空格；只输入空格仍属于有口令模式。"""
        image = make_image()
        mixed = core.scramble_image(image, " ", algorithm="row-pixel")
        self.assertTrue(core.image_requires_password(mixed))
        with self.assertRaises(core.ImageToolError):
            core.restore_image(mixed)
        self.assertEqual(core.restore_image(mixed, " ").tobytes(), image.tobytes())

    def test_public_pixel_damage_is_detected(self) -> None:
        """公开参数也应检测普通像素损坏，但不把它声称为防恶意伪造的认证。"""
        mixed = core.scramble_image(make_image(), algorithm="tomato")
        pixel = mixed.getpixel((0, 0))
        mixed.putpixel((0, 0), ((pixel[0] + 1) % 256, pixel[1], pixel[2], pixel[3]))
        with self.assertRaisesRegex(core.ImageToolError, "已改变"):
            core.restore_image(mixed)

    def test_public_mode_and_parameters_are_validated(self) -> None:
        """不接受未知无口令模式、缺字段或损坏的公开参数。"""
        mixed = core.scramble_image(make_image(), algorithm="block")
        valid = json.loads(mixed.info[core.METADATA_KEY])
        for field, value in (("key_mode", "password"), ("key_mode", False), ("parameters", []), ("salt", "0" * 32)):
            with self.subTest(field=field, value=value):
                changed = mixed.copy()
                metadata = valid.copy()
                metadata[field] = value
                changed.info[core.METADATA_KEY] = json.dumps(metadata)
                with self.assertRaises(core.ImageToolError):
                    core.restore_image(changed)
        metadata = valid.copy()
        metadata.pop("key_mode")
        mixed.info[core.METADATA_KEY] = json.dumps(metadata)
        with self.assertRaises(core.ImageToolError):
            core.restore_image(mixed)

    def test_password_is_not_stored_in_png(self) -> None:
        """有口令时不把原口令写入元数据，无口令时明确记录公开模式。"""
        password = "Do-not-store-this-password-2026"
        mixed = core.scramble_image(make_image(), password, algorithm="per-pixel")
        self.assertNotIn(password, mixed.info[core.METADATA_KEY])
        self.assertNotIn("key_mode", json.loads(mixed.info[core.METADATA_KEY]))

    def test_batch_without_password_restores_tree(self) -> None:
        """目录批量混淆和还原都可留空，同时保留层级和像素。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "子目录").mkdir()
            original = make_image()
            core.save_png(original, root / "子目录" / "a.png")
            mixed = batch.run_batch(batch.collect_images(root), algorithm="tomato")
            restored = batch.run_batch(batch.collect_images(mixed.output_directory), inverse=True)
            self.assertEqual((mixed.succeeded, mixed.failed, restored.succeeded, restored.failed), (1, 0, 1, 0))
            output = core.load_image(restored.output_directory / "子目录" / "a.png")
            self.assertEqual(output.tobytes(), original.tobytes())

    def test_mixed_batch_only_requires_password_for_protected_images(self) -> None:
        """同一批中无口令图自动处理，有口令图缺口令时失败、填正确后成功。"""
        image = make_image()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (("public.png", "", "tomato"), ("protected.png", "key", "block"), ("old.png", "key", core.ALGORITHM))
            for name, password, algorithm in cases:
                core.save_png(core.scramble_image(image, password, algorithm=algorithm), root / name)
            plan = batch.collect_images(root)
            empty = batch.run_batch(plan, inverse=True)
            self.assertEqual((empty.succeeded, empty.failed), (1, 2))
            correct = batch.run_batch(plan, "key", inverse=True)
            self.assertEqual((correct.succeeded, correct.failed), (3, 0))
            for source, target in correct.outputs:
                self.assertEqual(core.load_image(correct.output_directory / target).tobytes(), image.tobytes())

    def test_license_notice_remains_imported_without_a_gui_button(self) -> None:
        """主包导入完整 MIT 文本，确保移除按钮后仍随单 EXE 编入。"""
        self.assertIn("Copyright (c) 2025 uiloalxise", __third_party_notice__)
        self.assertIn("Permission is hereby granted, free of charge", __third_party_notice__)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', __third_party_notice__)


if __name__ == "__main__":
    unittest.main()
