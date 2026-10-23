"""批量处理回归：层级保留、自动编号、输出排除、停止与文件安全。"""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from confusing_images import batch, core


class BatchTests(unittest.TestCase):
    """仅操作独立临时目录，不处理用户真实图片。"""

    def setUp(self) -> None:
        """创建每个测试独立的中文输入目录。"""
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "输入 图片"
        self.root.mkdir()
        self.addCleanup(self.directory.cleanup)

    def write_image(self, relative: str, color: str = "#246BAD") -> Path:
        """按扩展名写入小型真实图片，供文件扫描和编码验证使用。"""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (33, 19), color)
        image.paste("#F6CB75", (3, 4, 18, 12))
        image.save(path)
        return path

    def test_scan_recursive_and_excludes_output(self) -> None:
        """支持中文、大小写扩展名和深层目录，整个根输出子树不参与扫描。"""
        self.write_image("a.JPG")
        self.write_image("人物/二级/b.png")
        self.write_image("confusing_images/旧结果.png")
        self.write_image("confusing_images/更深/旧结果.png")
        (self.root / "说明.txt").write_text("不是图片", encoding="utf-8")
        plan = batch.collect_images(self.root)
        self.assertEqual(set(plan.files), set([Path("a.JPG"), Path("人物/二级/b.png")]))
        self.assertEqual(plan.output_directory, self.root / "confusing_images")

    def test_selecting_directory_does_not_create_output(self) -> None:
        """选择和扫描阶段不产生目录或图片。"""
        self.write_image("a.png")
        plan = batch.collect_images(self.root)
        self.assertFalse(plan.output_directory.exists())

    def test_hierarchy_and_source_bytes_preserved(self) -> None:
        """输出路径保留完整相对层级，输入文件逐字节不变。"""
        paths = [self.write_image("a.jpg"), self.write_image("分类/二级/b.webp")]
        before = [path.read_bytes() for path in paths]
        result = batch.run_batch(batch.collect_images(self.root), "测试口令", algorithm="tomato")
        self.assertEqual((result.total, result.succeeded, result.failed), (2, 2, 0))
        self.assertTrue((result.output_directory / "a.png").is_file())
        self.assertTrue((result.output_directory / "分类/二级/b.png").is_file())
        self.assertEqual([path.read_bytes() for path in paths], before)
        for source, output in result.outputs:
            restored = core.restore_image(core.load_image(result.output_directory / output), "测试口令")
            self.assertEqual(restored.tobytes(), core.load_image(self.root / source).tobytes())

    def test_colliding_extensions_and_existing_results_are_numbered(self) -> None:
        """a.jpg、a.png 和已有 a.png/a_2.png 均不会互相覆盖。"""
        self.write_image("a.jpg")
        self.write_image("a.png", "#A73265")
        output = self.root / "confusing_images"
        output.mkdir()
        (output / "a.png").write_bytes(b"old png")
        (output / "a_2.png").write_bytes(b"old png two")
        result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual(result.succeeded, 2)
        self.assertEqual([str(path) for source, path in result.outputs], ["a_3.png", "a_4.png"])
        self.assertEqual((output / "a.png").read_bytes(), b"old png")
        self.assertEqual((output / "a_2.png").read_bytes(), b"old png two")

    def test_rerun_excludes_previous_outputs_and_keeps_them(self) -> None:
        """重复选择同一路径不会重新混淆输出图片，旧结果保持不变。"""
        self.write_image("folder/a.png")
        first = batch.run_batch(batch.collect_images(self.root), "key")
        old = first.output_directory / first.outputs[0][1]
        before = old.read_bytes()
        next_plan = batch.collect_images(self.root)
        self.assertEqual(len(next_plan.files), 1)
        second = batch.run_batch(next_plan, "key")
        self.assertEqual(second.succeeded, 1)
        self.assertEqual(second.outputs[0][1], Path("folder/a_2.png"))
        self.assertEqual(old.read_bytes(), before)

    def test_corrupt_file_failure_does_not_stop_later_files(self) -> None:
        """首张损坏图片失败后，后续合法图片仍正常保存。"""
        (self.root / "0broken.png").write_bytes(b"broken image")
        self.write_image("z-good.png")
        result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual((result.succeeded, result.failed, result.completed), (1, 1, 2))
        self.assertIn("0broken.png", result.errors[0])
        self.assertTrue((result.output_directory / "z-good.png").is_file())
        self.assertFalse((result.output_directory / "0broken.png").exists())

    def test_mixed_algorithms_restore_automatically(self) -> None:
        """同一文件夹不同版本和算法的混淆 PNG 均按自己的元数据还原。"""
        originals = []
        for relative, algorithm in (("a.png", core.ALGORITHM), ("分类/b.png", "block"), ("分类/c.png", "per-pixel")):
            original = Image.new("RGBA", (33, 19), (23, 111, 209, 73))
            original.paste((192, 57, 61, 175), (2, 3, 14, 10))
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            core.save_png(core.scramble_image(original, "key", algorithm=algorithm), path)
            originals.append((Path(relative), original))
        result = batch.run_batch(batch.collect_images(self.root), "key", inverse=True)
        self.assertEqual((result.succeeded, result.failed), (3, 0))
        for relative, original in originals:
            restored = core.load_image(result.output_directory / relative)
            self.assertEqual(restored.size, original.size)
            self.assertEqual(restored.tobytes(), original.tobytes())
            self.assertNotIn(core.METADATA_KEY, restored.info)

    def test_wrong_password_fails_each_file_without_output_images(self) -> None:
        """错误口令计入失败，不生成伪成功图片。"""
        source = self.write_image("a.png")
        core.save_png(core.scramble_image(core.load_image(source), "correct"), source)
        result = batch.run_batch(batch.collect_images(self.root), "wrong", inverse=True)
        self.assertEqual((result.succeeded, result.failed), (0, 1))
        self.assertIn("口令不正确", result.errors[0])
        self.assertFalse(list(result.output_directory.rglob("*.png")))

    def test_already_scrambled_input_can_be_scrambled_again(self) -> None:
        """批量混淆允许链式叠加：结果保存上一层参数，可逐层还原。"""
        source = self.write_image("a.png")
        original_bytes = core.load_image(source).tobytes()
        core.save_png(core.scramble_image(core.load_image(source), "key"), source)
        result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        # 叠加后的图片还原一层应回到上一层（仍带参数），再还原一层回到原图。
        layered = core.load_image(result.output_directory / result.outputs[0][1])
        one_back = core.restore_image(layered, "key")
        self.assertIn(core.METADATA_KEY, one_back.info)
        final = core.restore_image(one_back, "key")
        self.assertEqual(final.tobytes(), original_bytes)

    def test_cancel_before_start_writes_nothing(self) -> None:
        """开始前停止不创建输出目录。"""
        self.write_image("a.png")
        event = threading.Event()
        event.set()
        result = batch.run_batch(batch.collect_images(self.root), "key", cancel_event=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.completed, 0)
        self.assertFalse(result.output_directory.exists())

    def test_cancel_stops_submitting_new_images(self) -> None:
        """并行下取消后不再提交新任务；已提交的少量任务会完成并保存。"""
        for name in ("a.png", "b.png", "c.png", "d.png", "e.png", "f.png"):
            self.write_image(name)
        event = threading.Event()
        calls = []

        def report(value: float, text: str) -> None:
            """在第一张完成时请求停止，验证后续任务不再提交。"""
            calls.append(value)
            if value > 0:
                event.set()

        result = batch.run_batch(batch.collect_images(self.root), "key", progress=report, cancel_event=event, workers=2)
        self.assertTrue(result.cancelled)
        # 取消后不再提交新任务，已完成数量应小于总数，但已提交的会保存。
        self.assertLess(result.completed, result.total)
        self.assertGreaterEqual(result.succeeded, 1)
        self.assertEqual(calls, sorted(calls))
        self.assertFalse(list(result.output_directory.glob(".confusing-images-*")))

    def test_save_race_uses_next_number_without_overwriting(self) -> None:
        """模拟检查后出现同名文件，原子保存拒绝覆盖后自动改用下一编号。"""
        self.write_image("a.png")
        real_save = core.save_png
        attempts = []

        def racing_save(image: Image.Image, path: Path, *, overwrite: bool) -> Path:
            """第一次由其他进程占用目标，后续调用真实的禁止覆盖保存接口。"""
            attempts.append(path)
            if len(attempts) == 1:
                path.write_bytes(b"created by another process")
                raise FileExistsError(str(path))
            return real_save(image, path, overwrite=overwrite)

        with mock.patch("confusing_images.batch.save_png", side_effect=racing_save):
            result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual(result.outputs[0][1], Path("a_2.png"))
        self.assertEqual((result.output_directory / "a.png").read_bytes(), b"created by another process")

    def test_cancel_collects_running_success_failure_and_unverified_results(self) -> None:
        """停止时等待两个已运行任务，并核对保存文件、失败及未校验统计没有遗漏。"""
        for inverse, broken in ((False, False), (True, False), (False, True)):
            with self.subTest(inverse=inverse, broken=broken):
                prefix = f"case-{inverse}-{broken}"
                first = self.write_image(prefix + "/a.png")
                second = self.write_image(prefix + "/b.png")
                self.write_image(prefix + "/c.png")
                if broken:
                    second.write_bytes(b"broken image")
                plan = batch.collect_images(first.parent)
                event = threading.Event()
                started = threading.Barrier(2)
                real_process = batch._process_one

                def process(*args: object) -> tuple:
                    """确保两张均已运行；第二张等停止请求后才完成，稳定复现漏记。"""
                    started.wait(timeout=5)
                    if args[2].name == "b.png" and not event.wait(timeout=5):
                        raise RuntimeError("未收到停止请求")
                    return real_process(*args)

                def report(value: float, text: str) -> None:
                    """第一张统计完成后立即停止，第三张不得提交。"""
                    event.set()

                with mock.patch("confusing_images.batch._process_one", side_effect=process):
                    result = batch.run_batch(plan, inverse=inverse, workers=2, cancel_event=event, progress=report)
                saved = {path.relative_to(result.output_directory) for path in result.output_directory.rglob("*.png")}
                self.assertTrue(result.cancelled)
                self.assertEqual(result.completed, 2)
                self.assertEqual(result.succeeded, 1 if broken else 2)
                self.assertEqual(result.failed, int(broken))
                self.assertEqual(result.unverified, 2 if inverse else 0)
                self.assertEqual(len(result.unverified_notes), result.unverified)
                self.assertEqual(saved, {target for source, target in result.outputs})
                self.assertNotIn(Path("c.png"), saved)
                self.assertFalse(list(result.output_directory.rglob(".confusing-images-*")))

    def test_forbidden_overwrite_is_atomic_and_cleans_temp(self) -> None:
        """底层禁止覆盖保存不得损坏已有文件，并清理本次临时 PNG。"""
        image = Image.new("RGB", (5, 5), "red")
        output = self.root / "existing.png"
        output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            core.save_png(image, output, overwrite=False)
        self.assertEqual(output.read_bytes(), b"existing")
        self.assertFalse(list(self.root.glob(".confusing-images-*")))
        core.save_png(image, self.root / "new.png", overwrite=False)
        self.assertEqual(core.load_image(self.root / "new.png").tobytes(), image.tobytes())

    def test_output_root_file_is_not_overwritten(self) -> None:
        """confusing_images 已被普通文件占用时拒绝整个任务。"""
        self.write_image("a.png")
        output = self.root / "confusing_images"
        output.write_bytes(b"not a directory")
        with self.assertRaises(core.ImageToolError):
            batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual(output.read_bytes(), b"not a directory")

    def test_linked_subtrees_are_skipped(self) -> None:
        """链接判定也适用于 Windows 目录联接，不递归进入链接子树。"""
        self.write_image("linked/a.png")
        self.write_image("normal/b.png")
        real_check = batch._is_linklike
        with mock.patch("confusing_images.batch._is_linklike", side_effect=lambda path: path.name == "linked" or real_check(path)):
            plan = batch.collect_images(self.root)
        self.assertEqual(plan.files, (Path("normal/b.png"),))
        self.assertEqual(plan.linked_entries_skipped, 1)

    def test_linked_output_root_is_refused(self) -> None:
        """不能通过已有输出目录链接把图片写入选中路径之外。"""
        self.write_image("a.png")
        output = self.root / "confusing_images"
        output.mkdir()
        plan = batch.collect_images(self.root)
        real_check = batch._is_linklike
        with mock.patch("confusing_images.batch._is_linklike", side_effect=lambda path: path == output or real_check(path)):
            with self.assertRaises(core.ImageToolError):
                batch.run_batch(plan, "key")
        self.assertFalse(list(output.iterdir()))

    def test_relative_escape_and_invalid_plan_are_rejected(self) -> None:
        """伪造扫描快照不能读取上层或写入任意输出目录。"""
        self.write_image("a.png")
        bad = batch.BatchPlan(self.root, self.root / "confusing_images", (Path("../outside.png"),))
        result = batch.run_batch(bad, "key")
        self.assertEqual((result.succeeded, result.failed), (0, 1))
        plan = batch.BatchPlan(self.root, self.root / "elsewhere", (Path("a.png"),))
        with self.assertRaises(core.ImageToolError):
            batch.run_batch(plan, "key")

    def test_output_subdirectory_file_failure_continues(self) -> None:
        """输出子目录被文件占用时不覆盖它，其他图片继续保存。"""
        self.write_image("a.png")
        self.write_image("blocked/b.png")
        output = self.root / "confusing_images"
        output.mkdir()
        (output / "blocked").write_bytes(b"keep")
        result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual((result.succeeded, result.failed), (1, 1))
        self.assertEqual((output / "blocked").read_bytes(), b"keep")

    def test_empty_directory_and_invalid_root(self) -> None:
        """空目录不生成输出，非法根路径明确报错。"""
        result = batch.run_batch(batch.collect_images(self.root), "key")
        self.assertEqual(result.total, 0)
        self.assertFalse(result.output_directory.exists())
        with self.assertRaises(core.ImageToolError):
            batch.collect_images(self.root / "missing")

    def test_compatible_batch_is_unverified_and_restorable(self) -> None:
        """兼容算法也能批量自动保存，但不添加本工具校验参数。"""
        original = self.write_image("folder/a.png")
        mixed = batch.run_batch(batch.collect_images(self.root), "0.618", algorithm="tomato", compatible=True)
        output = mixed.output_directory / mixed.outputs[0][1]
        self.assertNotIn(core.METADATA_KEY, core.load_image(output).info)
        restored = batch.run_batch(batch.collect_images(mixed.output_directory), "0.618", algorithm="tomato", compatible=True, inverse=True)
        path = restored.output_directory / restored.outputs[0][1]
        self.assertEqual(core.load_image(path).tobytes(), core.load_image(original).tobytes())


if __name__ == "__main__":
    unittest.main()
