"""移植算法回归：八种无损适配、原格式限制及 Java 实测向量。"""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from confusing_images import core, upstream


def reference_image(width: int, height: int, mode: str) -> Image.Image:
    """按 Java 对照夹具相同的公式生成公开测试像素。"""
    indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
    red = (indices * 37 + 11) % 256
    green = (indices * 71 + 23) % 256
    blue = (indices * 97 + 31) % 256
    if mode == "RGBA":
        channels = (red, green, blue, (indices * 53 + 61) % 256)
    else:
        channels = (red, green, blue)
    return Image.fromarray(np.stack(channels, axis=2).astype(np.uint8))


class UpstreamTests(unittest.TestCase):
    """确保扩展算法保持旧格式兼容，并明确区分原项目已知的数据损失。"""

    def test_eight_native_algorithms_roundtrip(self) -> None:
        """五种尺寸和两种模式在全部八种本工具算法中均像素一致。"""
        for algorithm in upstream.ALGORITHM_IDS:
            for width, height in ((1, 1), (1, 19), (23, 1), (17, 13), (64, 48)):
                for mode in ("RGB", "RGBA"):
                    with self.subTest(algorithm=algorithm, size=(width, height), mode=mode):
                        original = reference_image(width, height, mode)
                        before = original.tobytes()
                        scrambled = core.scramble_image(original, "中文口令 test", algorithm=algorithm)
                        self.assertEqual(core.image_algorithm(scrambled), algorithm)
                        restored = core.restore_image(scrambled, "中文口令 test")
                        self.assertEqual((restored.size, restored.mode), (original.size, original.mode))
                        self.assertEqual(restored.tobytes(), before)
                        self.assertEqual(original.tobytes(), before)
                        self.assertNotIn(core.METADATA_KEY, restored.info)

    def test_eight_native_algorithms_disk_roundtrip(self) -> None:
        """每种算法的 PNG 参数真实写入磁盘后依然可验证还原。"""
        source = reference_image(39, 27, "RGBA")
        with tempfile.TemporaryDirectory() as directory:
            for algorithm in upstream.ALGORITHM_IDS:
                with self.subTest(algorithm=algorithm):
                    output = Path(directory) / f"{algorithm}.png"
                    core.save_png(core.scramble_image(source, "test", algorithm=algorithm), output)
                    restored = core.restore_image(core.load_image(output), "test")
                    self.assertEqual(restored.tobytes(), source.tobytes())

    def test_native_block_restores_original_size(self) -> None:
        """本工具 Block 记录补边前的尺寸，还原后自动裁回。"""
        source = reference_image(33, 17, "RGBA")
        mixed = core.scramble_image(source, "key", algorithm="block")
        self.assertEqual(mixed.size, (64, 32))
        self.assertEqual(json.loads(mixed.info[core.METADATA_KEY])["parameters"]["original_size"], [33, 17])
        restored = core.restore_image(mixed, "key")
        self.assertEqual(restored.size, source.size)
        self.assertEqual(restored.tobytes(), source.tobytes())

    def test_native_sort_metadata_larger_than_old_limit(self) -> None:
        """完整列映射可以超过旧版 8KB 上限，同时保持最大支持宽度可逆。"""
        source = reference_image(32_768, 2, "RGB")
        mixed = core.scramble_image(source, "wide", algorithm="sort")
        self.assertGreater(len(mixed.info[core.METADATA_KEY]), 8192)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wide.png"
            core.save_png(mixed, output)
            restored = core.restore_image(core.load_image(output), "wide")
            self.assertEqual(restored.tobytes(), source.tobytes())

    def test_native_integrity_and_password(self) -> None:
        """扩展算法也必须拒绝错误口令和修改后的像素或参数。"""
        source = reference_image(17, 13, "RGB")
        for algorithm in upstream.ALGORITHM_IDS:
            with self.subTest(algorithm=algorithm):
                mixed = core.scramble_image(source, "correct", algorithm=algorithm)
                with self.assertRaisesRegex(core.ImageToolError, "口令不正确"):
                    core.restore_image(mixed, "wrong")
                changed = mixed.copy()
                pixel = changed.getpixel((0, 0))
                changed.putpixel((0, 0), ((pixel[0] + 1) % 256, pixel[1], pixel[2]))
                with self.assertRaisesRegex(core.ImageToolError, "已改变"):
                    core.restore_image(changed, "correct")
                metadata = json.loads(mixed.info[core.METADATA_KEY])
                metadata["parameters"]["unexpected"] = True
                mixed.info[core.METADATA_KEY] = json.dumps(metadata)
                with self.assertRaisesRegex(core.ImageToolError, "已改变"):
                    core.restore_image(mixed, "correct")

    def test_compatible_sort_random_are_explicitly_lossy(self) -> None:
        """原项目两种末行格式可互通，但不能被误标记为本工具的无损 PNG。"""
        original = reference_image(17, 13, "RGBA")
        for algorithm in ("sort", "random"):
            with self.subTest(algorithm=algorithm):
                mixed = core.process_compatible(original, "123", algorithm)
                self.assertEqual(mixed.mode, "RGBA")
                self.assertNotIn(core.METADATA_KEY, mixed.info)
                restored = core.process_compatible(mixed, "ignored", algorithm, True)
                self.assertNotEqual(restored.tobytes(), original.tobytes())
                self.assertEqual(restored.crop((0, 0, 17, 12)).tobytes(), original.crop((0, 0, 17, 12)).tobytes())
                with self.assertRaises(core.ImageToolError):
                    core.restore_image(mixed, "123")

    def test_compatible_sort_mapping_rejects_corruption(self) -> None:
        """错误的原格式末行不能导致数组越界或重复索引覆盖。"""
        original = reference_image(17, 13, "RGBA")
        with self.assertRaises(core.ImageToolError):
            core.process_compatible(original, "", "sort", True)
        with self.assertRaises(core.ImageToolError):
            core.process_compatible(reference_image(17, 1, "RGB"), "", "sort")

    def test_compatible_numeric_key_validation(self) -> None:
        """拒绝原项目会静默回退或产生退化序列的密钥。"""
        source = reference_image(17, 13, "RGB")
        for key in ("hello", "nan", "inf", "0", "1", "-0.5"):
            with self.subTest(key=key):
                with self.assertRaises(core.ImageToolError):
                    core.process_compatible(source, key, "pic-row-column")
        with self.assertRaises(core.ImageToolError):
            core.process_compatible(source, "nan", "tomato")

    def test_unknown_algorithm_is_rejected(self) -> None:
        """未知算法不能进入带校验或兼容处理分支。"""
        source = reference_image(17, 13, "RGB")
        with self.assertRaises(core.ImageToolError):
            core.scramble_image(source, "key", algorithm="unknown")
        with self.assertRaises(core.ImageToolError):
            core.process_compatible(source, "key", "unknown")

    def test_gilbert_paths_are_permutations(self) -> None:
        """针对小型任意矩形检查曲线路径没有重复、越界或遗漏。"""
        for width in range(1, 19):
            for height in range(1, 19):
                path = upstream._gilbert_positions(width, height, None)
                self.assertEqual(sorted(path.tolist()), list(range(width * height)))

    def test_java_reference_vectors(self) -> None:
        """用实际原 Java 源码生成的固定指纹检测 Python 兼容性回归。"""
        fixture = Path(__file__).parent / "fixtures" / "upstream-vectors.tsv"
        with fixture.open(encoding="utf-8", newline="") as stream:
            self.assertIn("b573e0051d375a7a4c7ccfe7d7e385997b52348b", stream.readline())
            vectors = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(vectors), 26)
        for case in vectors:
            with self.subTest(case=case["id"]):
                source = reference_image(int(case["width"]), int(case["height"]), case["mode"])
                mixed = core.process_compatible(source, case["key"], case["algorithm"])
                restored = core.process_compatible(mixed, case["key"], case["algorithm"], True)
                cipher_hash = hashlib.sha256(mixed.convert("RGBA").tobytes()).hexdigest()
                restored_hash = hashlib.sha256(restored.convert("RGBA").tobytes()).hexdigest()
                self.assertEqual(cipher_hash, case["cipher_sha256"])
                self.assertEqual(restored_hash, case["restored_sha256"])


if __name__ == "__main__":
    unittest.main()
