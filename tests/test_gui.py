"""桌面界面回归测试：后台任务、模式切换、保存及输入保护。"""

from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

from PIL import Image

from confusing_images import core
from confusing_images.gui import ALGORITHM_LABELS, ImageToolApp


class GuiTests(unittest.TestCase):
    """在隐藏窗口中验证实际控件和后台线程的协作。"""

    def setUp(self) -> None:
        """创建独立 Tk 窗口、测试文件和不阻塞测试的消息框替身。"""
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = ImageToolApp(self.root)
        self.directory = tempfile.TemporaryDirectory()
        self.input_path = Path(self.directory.name) / "输入图片.png"
        image = Image.new("RGB", (61, 47), "#2B78AF")
        image.paste("#F3BC62", (5, 7, 35, 28))
        image.save(self.input_path)
        self.error_patch = mock.patch("confusing_images.gui.messagebox.showerror")
        self.warning_patch = mock.patch("confusing_images.gui.messagebox.showwarning")
        self.info_patch = mock.patch("confusing_images.gui.messagebox.showinfo")
        self.error = self.error_patch.start()
        self.warning = self.warning_patch.start()
        self.info = self.info_patch.start()
        self.addCleanup(self.error_patch.stop)
        self.addCleanup(self.warning_patch.stop)
        self.addCleanup(self.info_patch.stop)

    def tearDown(self) -> None:
        """等待任务结束后关闭窗口，避免残留线程和定时回调。"""
        self.wait_idle()
        self.app.result_image = None
        self.app._close()
        self.directory.cleanup()

    def wait_idle(self) -> None:
        """泵送真实 Tk 消息直到后台任务完成，超时则测试失败。"""
        deadline = time.monotonic() + 20
        while self.app._busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(self.app._busy, "后台任务超时")

    def load_fixture(self) -> None:
        """通过应用的真实后台读取入口载入图片。"""
        self.app.load_path(self.input_path)
        self.wait_idle()

    def load_saved_result(self) -> None:
        """将结果保存后重新选择图片，验证移除内存复用按钮后的实际操作。"""
        self.assertIsNotNone(self.app.result_image)
        path = Path(self.directory.name) / "重新选择的结果.png"
        compatible = self.app._result_compatibility
        next_mode = "restore" if self.app._result_mode == "scramble" else "scramble"
        core.save_png(self.app.result_image, path)
        self.app.load_path(path)
        self.wait_idle()
        if compatible:
            self.app.mode.set(next_mode)
            self.app._mode_changed()

    def select_all_files(self) -> None:
        """勾选目录列表中的全部图片。"""
        self.app.file_list.select_all_var.set(True)
        self.app.file_list._toggle_all()

    def test_initial_state(self) -> None:
        """没有输入或结果时处理、保存按钮均不可用。"""
        self.assertEqual(str(self.app.process_button["state"]), "disabled")
        self.assertEqual(str(self.app.save_button["state"]), "disabled")

    def test_file_selection_keyboard_and_busy_lock(self) -> None:
        """空格可切换当前行；忙碌时逐行切换和全选都不能修改待处理集合。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        panel = self.app.file_list
        first = panel.tree.get_children()[0]
        panel.tree.focus(first)
        panel._on_space(None)
        self.assertEqual(panel.checked_files(), [Path("输入图片.png")])
        self.app._busy = True
        self.app._set_controls()
        try:
            panel._on_space(None)
            panel._toggle_item(first)
            panel.select_all_var.set(False)
            panel._toggle_all()
            self.assertEqual(panel.checked_files(), [Path("输入图片.png")])
            self.assertEqual(str(panel.select_all_check["state"]), "disabled")
        finally:
            self.app._busy = False
            self.app._set_controls()
        panel._on_space(None)
        self.assertEqual(panel.checked_files(), [])

    def test_selected_long_path_is_available_without_truncating_data(self) -> None:
        """长文件名的完整相对路径可在只读框查看、移动光标和复制。"""
        panel = self.app.file_list
        relative = Path("很长的目录名" * 12) / ("长文件名" * 20 + ".png")
        panel.populate([relative])
        panel.tree.selection_set("file-0")
        panel._show_path()
        self.assertEqual(panel.path_hint.get(), str(relative))
        self.assertEqual(str(panel.path_hint["state"]), "readonly")

    def test_empty_password_creates_public_image(self) -> None:
        """不填口令可以直接处理，生成可无口令还原的图片。"""
        self.load_fixture()
        self.app.process_image()
        self.wait_idle()
        self.assertIsNotNone(self.app.result_image)
        self.assertFalse(core.image_requires_password(self.app.result_image))
        self.warning.assert_not_called()

    def test_gui_scramble_and_restore(self) -> None:
        """完成载入、混淆、保存后重选和还原的真实界面流程。"""
        self.load_fixture()
        original = self.app.input_image.tobytes()
        self.app.password.set("中文 GUI Test")
        self.app.process_image()
        self.assertEqual(str(self.app.open_button["state"]), "disabled")
        self.wait_idle()
        self.assertIsNotNone(self.app.result_image)
        self.assertNotEqual(original, self.app.result_image.tobytes())
        self.load_saved_result()
        self.assertEqual(self.app.mode.get(), "restore")
        self.assertIsNone(self.app.result_image)
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(original, self.app.result_image.tobytes())
        self.assertIn("像素校验通过", self.app.status.get())
        self.error.assert_not_called()

    def test_gui_save_and_reload(self) -> None:
        """保存后的文件可重新载入，并自动识别为还原模式。"""
        self.load_fixture()
        self.app.password.set("save test")
        self.app.process_image()
        self.wait_idle()
        output = Path(self.directory.name) / "输出 混淆图.png"
        with mock.patch("confusing_images.gui.filedialog.asksaveasfilename", return_value=str(output)):
            self.app.save_result()
        self.wait_idle()
        self.assertTrue(output.is_file())
        self.app.load_path(output)
        self.wait_idle()
        self.assertEqual(self.app.mode.get(), "restore")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(core.load_image(self.input_path).tobytes(), self.app.result_image.tobytes())
        self.error.assert_not_called()

    def load_layered_fixture(self) -> Image.Image:
        """载入三层相同算法的真实 PNG，返回原图供连续点击回归比较。"""
        original = core.load_image(self.input_path)
        layered = original
        for index in range(3):
            layered = core.scramble_image(layered, algorithm="tomato")
        core.save_png(layered, self.input_path)
        self.load_fixture()
        return original

    def test_repeated_restore_clicks_advance_and_stop_at_original(self) -> None:
        """连续点击每次还原一层，到原图后禁用按钮并阻止快捷键再次处理。"""
        original = self.load_layered_fixture()
        loaded = self.app.input_image
        for index in range(3):
            self.app.process_image()
            self.wait_idle()
            if index < 2:
                self.assertIn(core.METADATA_KEY, self.app.result_image.info)
            else:
                self.assertNotIn(core.METADATA_KEY, self.app.result_image.info)
        self.assertEqual(self.app.result_image.tobytes(), original.tobytes())
        self.assertIs(self.app.input_image, loaded)
        self.assertEqual(str(self.app.process_button["state"]), "disabled")
        final = self.app.result_image
        self.app.process_image()
        self.assertFalse(self.app._busy)
        self.assertIs(self.app.result_image, final)
        self.error.assert_not_called()

    def test_chain_updates_algorithm_password_and_preserves_retry_result(self) -> None:
        """混合算法和口令逐层切换，错误口令保留中间结果，可保存并重试。"""
        original = core.load_image(self.input_path)
        inner = core.scramble_image(original, "inner key", algorithm="block")
        middle = core.scramble_image(inner, algorithm="per-pixel")
        outer = core.scramble_image(middle, "outer key", algorithm="tomato")
        core.save_png(outer, self.input_path)
        original_file = self.input_path.read_bytes()
        self.load_fixture()
        self.app.password.set("outer key")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app._algorithm_id(), "per-pixel")
        self.assertEqual(str(self.app.password_entry["state"]), "disabled")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), inner.tobytes())
        self.assertEqual(self.app._algorithm_id(), "block")
        self.assertEqual(str(self.app.password_entry["state"]), "normal")
        previous = self.app.result_image
        self.app.password.set("wrong key")
        self.assertIs(self.app.result_image, previous)
        self.app.process_image()
        self.wait_idle()
        self.error.assert_called_once()
        self.assertIs(self.app.result_image, previous)
        output = Path(self.directory.name) / "中间层.png"
        with mock.patch("confusing_images.gui.filedialog.asksaveasfilename", return_value=str(output)):
            self.app.save_result()
        self.wait_idle()
        self.assertEqual(core.load_image(output).info[core.METADATA_KEY], inner.info[core.METADATA_KEY])
        self.app.password.set("inner key")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), original.tobytes())
        self.assertEqual(self.input_path.read_bytes(), original_file)

    def test_chain_cursor_resets_when_mode_or_file_changes(self) -> None:
        """切换模式或重新选图后从左侧输入重新开始，避免沿用旧图的还原进度。"""
        self.load_layered_fixture()
        self.app.process_image()
        self.wait_idle()
        first = self.app.result_image.info[core.METADATA_KEY]
        self.app.mode.set("scramble")
        self.app._mode_changed()
        self.app.mode.set("restore")
        self.app._mode_changed()
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.info[core.METADATA_KEY], first)
        self.load_fixture()
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.info[core.METADATA_KEY], first)
        self.error.assert_not_called()

    def test_chain_manual_mode_uses_loaded_input_and_resets_progress(self) -> None:
        """手动模式仍针对左侧输入，切回自动后重新从载入图片还原。"""
        self.load_layered_fixture()
        self.app.process_image()
        self.wait_idle()
        first = self.app.result_image.info[core.METADATA_KEY]
        self.app.compatibility.set(True)
        self.app._format_changed()
        self.app.password.set("0.618")
        expected = core.restore_selected_image(self.app.input_image, "0.618", manual=True)
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), expected.tobytes())
        self.assertEqual(self.app.result_image.info[core.RESTORE_STATUS], "unverified")
        self.app.compatibility.set(False)
        self.app._format_changed()
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.info[core.METADATA_KEY], first)
        self.error.assert_not_called()

    def test_overwriting_input_is_blocked(self) -> None:
        """用户选择输入图片作为保存目标时必须拒绝覆盖。"""
        self.load_fixture()
        original_file = self.input_path.read_bytes()
        self.app.password.set("protect source")
        self.app.process_image()
        self.wait_idle()
        with mock.patch("confusing_images.gui.filedialog.asksaveasfilename", return_value=str(self.input_path)):
            self.app.save_result()
        self.warning.assert_called_once()
        self.assertEqual(self.input_path.read_bytes(), original_file)

    def test_background_error_returns_to_idle(self) -> None:
        """错误口令不应卡死界面，控件应恢复可用。"""
        self.load_fixture()
        self.app.password.set("correct")
        self.app.process_image()
        self.wait_idle()
        self.load_saved_result()
        self.app.password.set("wrong")
        self.app.process_image()
        self.wait_idle()
        self.error.assert_called_once()
        self.assertIsNone(self.app.result_image)
        self.assertEqual(str(self.app.process_button["state"]), "normal")

    def test_mode_change_discards_stale_result(self) -> None:
        """切换模式后不允许误保存旧结果。"""
        self.load_fixture()
        self.app.password.set("test")
        self.app.process_image()
        self.wait_idle()
        self.app.mode.set("restore")
        self.app._mode_changed()
        self.assertIsNone(self.app.result_image)
        self.assertEqual(str(self.app.save_button["state"]), "disabled")

    def test_failed_load_preserves_current_input(self) -> None:
        """新图片读取失败时仍保留已载入的输入图片。"""
        self.load_fixture()
        original = self.app.input_image
        self.app.load_path(Path(self.directory.name) / "missing.png")
        self.wait_idle()
        self.assertIs(self.app.input_image, original)
        self.error.assert_called_once()

    def test_password_change_discards_stale_result(self) -> None:
        """口令变化后必须重新处理，不能误保存旧口令的结果。"""
        self.load_fixture()
        self.app.password.set("old password")
        self.app.process_image()
        self.wait_idle()
        self.app.password.set("new password")
        self.assertIsNone(self.app.result_image)
        self.assertEqual(str(self.app.save_button["state"]), "disabled")

    def test_native_algorithm_auto_detects_and_locks(self) -> None:
        """新算法图片重新载入后自动识别算法，不能误选其他算法还原。"""
        self.load_fixture()
        self.app.algorithm_name.set(ALGORITHM_LABELS["tomato"])
        self.app._algorithm_changed()
        self.assertFalse(hasattr(self.app, "strength_box"))
        self.app.password.set("any password")
        self.app.process_image()
        self.wait_idle()
        self.load_saved_result()
        self.assertEqual(self.app._algorithm_id(), "tomato")
        self.assertEqual(str(self.app.algorithm_box["state"]), "disabled")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), core.load_image(self.input_path).tobytes())

    def test_compatible_mode_does_not_claim_integrity(self) -> None:
        """原格式处理必须显示未校验，不能误报像素校验通过。"""
        self.load_fixture()
        self.app.compatibility.set(True)
        self.app._format_changed()
        self.app.password.set("0.618")
        original = self.app.input_image.tobytes()
        self.app.process_image()
        self.wait_idle()
        self.assertNotIn(core.METADATA_KEY, self.app.result_image.info)
        self.assertIn("未验证", self.app.status.get())
        self.load_saved_result()
        self.assertEqual(self.app.mode.get(), "restore")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(original, self.app.result_image.tobytes())
        self.assertNotIn("像素校验通过", self.app.status.get())

    def test_compatible_sort_allows_no_key_and_warns(self) -> None:
        """无密钥的 Sort 兼容操作仍需确认末行有损风险。"""
        self.load_fixture()
        self.app.compatibility.set(True)
        self.app.algorithm_name.set(ALGORITHM_LABELS["sort"])
        self.app._format_changed()
        self.assertEqual(str(self.app.password_entry["state"]), "disabled")
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True) as confirm:
            self.app.process_image()
            self.wait_idle()
        confirm.assert_called_once()
        self.assertIsNotNone(self.app.result_image)
        self.error.assert_not_called()

    def test_native_png_can_be_forced_manual(self) -> None:
        """已有本工具参数的图片默认自动还原；勾选强制手动可忽略参数按所选算法尝试。"""
        self.load_fixture()
        self.app.password.set("key")
        self.app.process_image()
        self.wait_idle()
        self.load_saved_result()
        # 默认情况下不应误用原始密钥：带参数时自动还原，切换兼容框仍会调用自动识别逻辑。
        self.app.compatibility.set(True)
        self.app._format_changed()
        # 现在允许用户显式忽略嵌入参数（这是本需求新增的“不做限制”行为）。
        self.assertTrue(self.app.compatibility.get())
        self.warning.assert_not_called()

    def test_batch_directory_scan_and_auto_save(self) -> None:
        """文件夹模式列出图片，全选后处理，完成后显示统计。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.assertIsNotNone(self.app.batch_plan)
        self.assertIsNone(self.app.input_image)
        self.assertEqual(len(self.app.batch_plan.files), 1)
        self.assertFalse(self.app.batch_plan.output_directory.exists())
        self.select_all_files()
        self.app.password.set("batch key")
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
            self.app.process_image()
            self.assertEqual(str(self.app.folder_button["state"]), "disabled")
            self.wait_idle()
        result = self.app.batch_result
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        self.assertTrue((result.output_directory / "输入图片.png").is_file())
        self.assertEqual(str(self.app.save_button["state"]), "disabled")
        self.assertEqual(str(self.app.batch_details_button["state"]), "normal")
        self.assertIs(self.app.result_preview._summary, result)
        self.app.mode.set("restore")
        self.app._mode_changed()
        self.assertIsNone(self.app.result_preview._summary)
        self.info.assert_called_once()
        self.error.assert_not_called()

    def test_batch_partial_selection_processes_only_checked(self) -> None:
        """勾选部分图片时只处理勾选项，未勾选的不输出。"""
        Image.new("RGB", (25, 17), "red").save(Path(self.directory.name) / "b.png")
        Image.new("RGB", (25, 17), "green").save(Path(self.directory.name) / "c.png")
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        # 只勾选第一张，其余不勾选。
        first_iid = self.app.file_list.tree.get_children()[0]
        self.app.file_list._checked.add(first_iid)
        self.app.file_list._refresh()
        self.assertEqual(len(self.app.file_list.checked_files()), 1)
        self.app.password.set("key")
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
            self.app.process_image()
            self.wait_idle()
        result = self.app.batch_result
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        self.assertEqual(len(result.outputs), 1)
        # 输出目录只应有勾选那一张，未勾选的不处理。
        self.assertEqual(len(list(result.output_directory.rglob("*.png"))), 1)

    def test_batch_requires_selection_warning(self) -> None:
        """未勾选任何图片时提示，不启动处理。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.app.password.set("key")
        self.app.process_image()
        self.warning.assert_called_once()
        self.assertFalse(self.app._busy)
        self.assertFalse(self.app.batch_plan.output_directory.exists())

    def test_batch_restore_auto_detects_without_single_image(self) -> None:
        """批量还原时算法框保持可选，供无参数图片手动选择；有参数图片仍自动还原。"""
        original = core.load_image(self.input_path)
        core.save_png(core.scramble_image(original, "key", algorithm="tomato"), self.input_path)
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.app.mode.set("restore")
        self.app._mode_changed()
        # 批量目录可能混合有/无参数图片，算法框保留可选手动算法。
        self.assertEqual(str(self.app.algorithm_box["state"]), "readonly")
        self.select_all_files()
        self.app.password.set("key")
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
            self.app.process_image()
            self.wait_idle()
        result = self.app.batch_result
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(core.load_image(result.output_directory / "输入图片.png").tobytes(), original.tobytes())

    def test_force_manual_allows_ignoring_embedded_metadata(self) -> None:
        """勾选强制手动后，带参数的图片也按所选算法尝试并标记未校验。"""
        self.load_fixture()
        self.app.algorithm_name.set(ALGORITHM_LABELS["block"])
        self.app._algorithm_changed()
        self.app.password.set("key")
        self.app.process_image()
        self.wait_idle()
        self.load_saved_result()
        # 勾选强制手动后不再被自动取消，允许忽略嵌入参数按当前算法尝试。
        self.app.compatibility.set(True)
        self.app._format_changed()
        self.assertTrue(self.app.compatibility.get())
        self.app.password.set("key")
        self.app.process_image()
        self.wait_idle()
        self.assertIsNotNone(self.app.result_image)
        self.assertIn("未校验", self.app.status.get())

    def test_batch_cancel_confirmation_writes_nothing(self) -> None:
        """拒绝开始确认时，目录和结果均不写入。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.select_all_files()
        self.app.password.set("key")
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=False):
            self.app.process_image()
        self.assertFalse(self.app._busy)
        self.assertFalse(self.app.batch_plan.output_directory.exists())

    def test_batch_stop_counts_all_running_images(self) -> None:
        """点击停止后收集两张已运行图片，未提交的第三张不处理，统计与磁盘一致。"""
        Image.new("RGB", (25, 17), "red").save(Path(self.directory.name) / "second.png")
        Image.new("RGB", (25, 17), "blue").save(Path(self.directory.name) / "third.png")
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.select_all_files()
        self.app.password.set("key")
        started = threading.Event()
        both_running = threading.Barrier(2)
        release = threading.Event()
        real_scramble = core.scramble_image

        def delayed_scramble(*args: object, **kwargs: object) -> Image.Image:
            """两张均进入处理后再允许停止，避免线程调度使断言偶然通过。"""
            both_running.wait(timeout=5)
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("测试同步超时")
            return real_scramble(*args, **kwargs)

        with mock.patch("confusing_images.batch.scramble_image", side_effect=delayed_scramble), mock.patch("confusing_images.batch.os.cpu_count", return_value=2):
            with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
                self.app.process_image()
            try:
                self.assertTrue(started.wait(timeout=5))
                self.app.stop_batch()
            finally:
                release.set()
            self.wait_idle()
        result = self.app.batch_result
        self.assertTrue(result.cancelled)
        self.assertIs(self.app.result_preview._summary, result)
        self.assertEqual((result.succeeded, result.failed, result.completed), (2, 0, 2))
        saved = {path.relative_to(result.output_directory) for path in result.output_directory.rglob("*.png")}
        self.assertEqual(saved, {target for source, target in result.outputs})
        self.assertAlmostEqual(self.app.progress.get(), 100 * 2 / 3)
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        self.assertEqual(str(self.app.folder_button["state"]), "normal")

    def test_select_single_image_exits_batch_mode(self) -> None:
        """目录模式后再次选择单图恢复原有预览、保存和按钮语义。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.app.load_path(self.input_path)
        self.wait_idle()
        self.assertIsNone(self.app.batch_plan)
        self.assertIsNotNone(self.app.input_image)
        self.assertEqual(str(self.app.process_button["text"]), "开始混淆")
        self.assertNotIn("自动保存", self.app.result_preview._empty_text)

    def test_cancel_folder_selection_preserves_single_input(self) -> None:
        """取消文件夹对话框时，不改变已经载入的图片。"""
        self.load_fixture()
        original = self.app.input_image
        with mock.patch("confusing_images.gui.filedialog.askdirectory", return_value=""):
            self.app.choose_directory()
        self.assertIs(self.app.input_image, original)
        self.assertIsNone(self.app.batch_plan)

    def test_simplified_interface_has_only_eight_algorithms(self) -> None:
        """界面不再提供旧算法、强度、开源按钮及内存复用按钮。"""
        self.assertEqual(len(self.app.algorithm_box["values"]), 8)
        self.assertNotIn(ALGORITHM_LABELS[core.ALGORITHM], self.app.algorithm_box["values"])
        self.assertEqual(self.app._algorithm_id(), "tomato")
        self.assertFalse(hasattr(self.app, "strength_box"))
        self.assertFalse(hasattr(self.app, "reuse_button"))
        self.assertFalse(hasattr(self.app, "use_result"))
        self.assertFalse(hasattr(self.app, "show_notices"))
        self.assertEqual(str(self.app.password_entry["state"]), "normal")

    def test_public_png_auto_disables_unused_password(self) -> None:
        """无口令 PNG 重新打开后直接还原，残留的其他口令不会造成失败。"""
        self.load_fixture()
        original = self.app.input_image.tobytes()
        self.app.process_image()
        self.wait_idle()
        self.load_saved_result()
        self.assertEqual(str(self.app.password_entry["state"]), "disabled")
        self.assertIn("无需口令", self.app.password_caption.get())
        self.app.password.set("其他图片的残留口令")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), original)
        self.error.assert_not_called()

    def test_old_algorithm_is_restore_only(self) -> None:
        """旧版图片仍自动识别并使用原口令还原，但不能从菜单新建旧算法图片。"""
        original = core.load_image(self.input_path)
        core.save_png(core.scramble_image(original, "old key"), self.input_path)
        self.load_fixture()
        self.assertEqual(self.app._algorithm_id(), core.ALGORITHM)
        self.assertEqual(str(self.app.algorithm_box["state"]), "disabled")
        self.app.password.set("old key")
        self.app.process_image()
        self.wait_idle()
        self.assertEqual(self.app.result_image.tobytes(), original.tobytes())
        self.app.mode.set("scramble")
        self.app._mode_changed()
        self.assertEqual(self.app._algorithm_id(), "tomato")

    def test_batch_can_scramble_with_empty_password(self) -> None:
        """批量模式也允许留空口令，保存的每张 PNG 均不要求口令。"""
        self.app.load_directory(Path(self.directory.name))
        self.wait_idle()
        self.select_all_files()
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
            self.app.process_image()
            self.wait_idle()
        result = self.app.batch_result
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        output = core.load_image(result.output_directory / result.outputs[0][1])
        self.assertFalse(core.image_requires_password(output))
        self.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
