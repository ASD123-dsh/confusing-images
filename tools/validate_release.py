"""生成自绘验证图，检查像素往返、GUI 预览及独立 EXE 启动。"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import tkinter as tk
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageGrab

from confusing_images import batch, core, upstream
from confusing_images.gui import ALGORITHM_LABELS, ImageToolApp
from confusing_images.third_party import NOTICE

ROOT = Path(__file__).resolve().parents[1]
DEMO_PASSWORD = "Demo-2026"


def make_example() -> Image.Image:
    """绘制原创山水和色块测试图，不读取或修改用户参考图片。"""
    width, height = 960, 640
    vertical = np.linspace(0, 1, height)[:, None, None]
    top = np.array([176, 211, 229])[None, None, :]
    bottom = np.array([245, 222, 186])[None, None, :]
    pixels = np.repeat((top * (1 - vertical) + bottom * vertical).astype(np.uint8), width, axis=1)
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    draw.ellipse((646, 72, 782, 208), fill="#F8CC73")
    draw.polygon([(0, 374), (210, 135), (385, 352), (551, 223), (775, 409)], fill="#7D9A9B")
    draw.polygon([(110, 249), (210, 135), (306, 254), (231, 226), (209, 204), (175, 242)], fill="#E9E7DB")
    draw.polygon([(380, 402), (689, 222), (960, 384), (960, 512)], fill="#477F7D")
    draw.polygon([(0, 388), (302, 338), (581, 449), (791, 408), (960, 467), (960, 640), (0, 640)], fill="#336268")
    draw.polygon([(0, 475), (320, 450), (960, 545), (960, 640), (0, 640)], fill="#9BB8B5")
    draw.polygon([(456, 478), (509, 471), (761, 640), (482, 640)], fill="#E7D1AE")
    for left, top_y, scale in ((95, 381, 1.0), (165, 403, 0.8), (814, 383, 1.1), (875, 419, 0.7)):
        draw.rectangle((left - 3, top_y, left + 3, top_y + int(118 * scale)), fill="#4C5147")
        for offset in range(3):
            tree_top = top_y - int(50 * scale) + int(offset * 26 * scale)
            half = int((35 + offset * 6) * scale)
            draw.polygon([(left, tree_top), (left - half, tree_top + 65 * scale), (left + half, tree_top + 65 * scale)], fill="#244D53")
    draw.rounded_rectangle((34, 30, 394, 98), radius=12, fill="#F4F1E6")
    draw.text((56, 47), "CONFUSING IMAGES", fill="#274E61", font_size=23)
    draw.text((57, 77), "LOCAL  /  REVERSIBLE  /  PNG", fill="#5D797E", font_size=10)
    return image


def generate_examples(folder: Path) -> dict:
    """从真实 PNG 文件生成混淆/还原样例，并对中等大图计时。"""
    image = make_example()
    original_path = folder / "example-original.png"
    scrambled_path = folder / "example-scrambled.png"
    restored_path = folder / "example-restored.png"
    core.save_png(image, original_path)
    source = core.load_image(original_path)
    scrambled = core.scramble_image(source, DEMO_PASSWORD, algorithm="tomato")
    core.save_png(scrambled, scrambled_path)
    restored = core.restore_image(core.load_image(scrambled_path), DEMO_PASSWORD)
    core.save_png(restored, restored_path)
    if source.tobytes() != core.load_image(restored_path).tobytes():
        raise AssertionError("示例图片磁盘往返像素不一致")
    benchmark = source.resize((960, 640), Image.Resampling.LANCZOS)
    started = time.perf_counter()
    result = core.scramble_image(benchmark, DEMO_PASSWORD, algorithm="tomato")
    scrambled_seconds = time.perf_counter() - started
    started = time.perf_counter()
    recovered = core.restore_image(result, DEMO_PASSWORD)
    restored_seconds = time.perf_counter() - started
    if benchmark.tobytes() != recovered.tobytes():
        raise AssertionError("大图往返像素不一致")
    algorithm_folder = folder / "algorithms"
    algorithm_folder.mkdir(exist_ok=True)
    examples = []
    for algorithm in upstream.ALGORITHM_IDS:
        mixed = core.scramble_image(source, algorithm=algorithm)
        path = algorithm_folder / (algorithm + ".png")
        core.save_png(mixed, path)
        restored = core.restore_image(core.load_image(path))
        if restored.tobytes() != source.tobytes() or restored.size != source.size:
            raise AssertionError("新增算法示例还原不一致：" + algorithm)
        examples.append((algorithm, mixed))
    overview = Image.new("RGB", (1344, 508), "#F3F6FA")
    draw = ImageDraw.Draw(overview)
    for index, (algorithm, mixed) in enumerate(examples):
        column, row = index % 4, index // 4
        left, top_y = column * 336, row * 254
        thumb = mixed.convert("RGB")
        thumb.thumbnail((316, 214), Image.Resampling.LANCZOS)
        overview.paste(thumb, (left + (336 - thumb.width) // 2, top_y + 30))
        draw.text((left + 12, top_y + 7), algorithm, fill="#172D49", font_size=17)
    overview_path = folder / "algorithms-overview.png"
    overview.save(overview_path)
    return dict(
        original=str(original_path), scrambled=str(scrambled_path), restored=str(restored_path),
        demo_password=DEMO_PASSWORD, pixels_equal=True,
        benchmark_size=list(benchmark.size), benchmark_algorithm="tomato",
        scramble_seconds=round(scrambled_seconds, 3), restore_seconds=round(restored_seconds, 3),
        additional_algorithms_verified=list(upstream.ALGORITHM_IDS), overview=str(overview_path),
        public_algorithms_verified=list(upstream.ALGORITHM_IDS),
    )


def validate_batch(folder: Path) -> dict:
    """用自绘图片验证批量层级、冲突编号、重复扫描和自动还原。"""
    with tempfile.TemporaryDirectory(prefix="batch-check-", dir=folder) as directory:
        root = Path(directory)
        picture = make_example().resize((96, 64))
        (root / "分类" / "二级").mkdir(parents=True)
        for relative in ("a.jpg", "a.png", "分类/二级/b.png"):
            picture.save(root / relative)
        plan = batch.collect_images(root)
        first = batch.run_batch(plan, DEMO_PASSWORD, algorithm="tomato")
        previous = dict((relative, (first.output_directory / relative).read_bytes()) for source, relative in first.outputs)
        repeat_plan = batch.collect_images(root)
        if repeat_plan.files != plan.files:
            raise AssertionError("批量扫描未排除输出目录")
        second = batch.run_batch(repeat_plan, DEMO_PASSWORD, algorithm="tomato")
        if (first.succeeded, second.succeeded, first.failed, second.failed) != (3, 3, 0, 0):
            raise AssertionError("批量混淆示例未全部成功")
        if any((first.output_directory / path).read_bytes() != content for path, content in previous.items()):
            raise AssertionError("重复批量处理覆盖了旧结果")
        public = batch.run_batch(plan, algorithm="tomato")
        if public.succeeded != 3 or public.failed:
            raise AssertionError("无口令批量混淆示例失败")
        expected = dict()
        for source, target in first.outputs + second.outputs + public.outputs:
            expected[target] = core.load_image(root / source).tobytes()
        recovery_plan = batch.collect_images(first.output_directory)
        without_password = batch.run_batch(recovery_plan, inverse=True)
        if (without_password.succeeded, without_password.failed) != (3, 6):
            raise AssertionError("无口令模式未正确区分设有口令的图片")
        recovered = batch.run_batch(recovery_plan, DEMO_PASSWORD, inverse=True)
        if recovered.succeeded != 9 or recovered.failed:
            raise AssertionError("批量还原示例失败")
        for source, target in recovered.outputs:
            if core.load_image(recovered.output_directory / target).tobytes() != expected[source]:
                raise AssertionError("批量还原像素不一致")
    return dict(input_images=3, first_saved=3, rerun_saved=3, public_saved=3, restored=9, without_password_restored=3, protected_rejected_without_password=6, tree_preserved=True, output_excluded=True, collisions_numbered=True, pixels_equal=True)


def capture_gui_preview(folder: Path, source: Path, *, batch_mode: bool = False) -> Path:
    """运行实际单图或目录界面完成混淆，仅截取本程序窗口。"""
    root = tk.Tk()
    app = ImageToolApp(root)
    errors = []
    phase = ["load"]
    screenshot = folder / ("batch-preview.png" if batch_mode else "gui-preview.png")
    directory = folder / "batch-example" / "input"
    if batch_mode:
        (directory / "分类" / "二级").mkdir(parents=True, exist_ok=True)
        with Image.open(source) as picture:
            for relative in ("a.jpg", "a.png", "分类/二级/b.png"):
                picture.save(directory / relative)

    def fail(message: str) -> None:
        """保存诊断错误并终止本次验证窗口。"""
        errors.append(message)
        root.destroy()

    def capture() -> None:
        """按本程序窗口句柄截图，避免桌面遮挡混入其他应用内容。"""
        root.update_idletasks()
        # 修复桌面矩形截图可能包含遮挡窗口的问题：仅向本程序 HWND 请求绘制。
        handle = int(str(root.tk.call("wm", "frame", root._w)), 0)
        ImageGrab.grab(window=handle).save(screenshot)
        app.result_image = None
        root.after_cancel(watchdog)
        app._close()

    def advance() -> None:
        """通过 Tk 定时器等待后台任务，主线程持续处理窗口消息。"""
        if errors:
            root.destroy()
            return
        if not app._busy:
            loaded = app.batch_plan is not None if batch_mode else app.input_image is not None
            if phase[0] == "load" and loaded:
                app.password.set("")
                app.algorithm_name.set(ALGORITHM_LABELS["tomato"])
                app._algorithm_changed()
                if batch_mode:
                    # 目录模式下需先全选文件列表再处理。
                    app.file_list.select_all_var.set(True)
                    app.file_list._toggle_all()
                app.process_image()
                phase[0] = "scramble"
            elif phase[0] == "scramble" and (app.batch_result is not None if batch_mode else app.result_image is not None):
                if batch_mode and (app.batch_result.succeeded != 3 or app.batch_result.failed):
                    fail("目录界面未成功保存三张示例图片")
                    return
                root.after(350, capture)
                return
        root.after(80, advance)

    with mock.patch("confusing_images.gui.messagebox.showerror", side_effect=lambda title, message, **kwargs: errors.append(message)):
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True):
            with mock.patch("confusing_images.gui.messagebox.showinfo"):
                watchdog = root.after(25_000, lambda: fail("界面预览验证超时"))
                if batch_mode:
                    app.load_directory(directory)
                else:
                    app.load_path(source)
                root.after(80, advance)
                root.mainloop()
    if errors:
        raise RuntimeError("；".join(errors))
    if not screenshot.exists():
        raise RuntimeError("未生成界面预览截图")
    return screenshot


class ProcessEntry(ctypes.Structure):
    """定义 Windows Toolhelp 进程条目，用于识别单文件 EXE 的子进程。"""

    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


def process_family(parent_pid: int) -> set[int]:
    """枚举本次启动的进程及其后代，不匹配或关闭其他程序。"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = dict()
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    family = set([parent_pid])
    changed = True
    while changed:
        added = set(pid for pid, parent in parents.items() if parent in family) - family
        changed = bool(added)
        family.update(added)
    return family


def find_app_window(pids: set[int]) -> int | None:
    """寻找属于本次 EXE 进程树且标题正确的可见主窗口。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    matches = []

    def visit(handle: int, extra: int) -> bool:
        """只收集本工具的可见窗口，不对其他窗口发送消息。"""
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(handle):
            text = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(handle, text, len(text))
            if "混淆图工具" in text.value and "Confusing Images" in text.value:
                matches.append(handle)
        return True

    user.EnumWindows(callback_type(visit), 0)
    return matches[0] if matches else None


def verify_embedded_notice(executable: Path) -> bool:
    """只读提取成品归档的许可常量，确认删除界面按钮后仍随 EXE 分发。"""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    name = next(name for name, entry in archive.toc.items() if entry[-1] == "z")
    python_archive = archive.open_embedded_archive(name)
    code = python_archive.extract("confusing_images.third_party")
    constants = "\n".join(value for value in code.co_consts if isinstance(value, str))
    if NOTICE not in constants:
        raise AssertionError("成品未包含完整第三方版权和 MIT 许可")
    return True


def smoke_executable(executable: Path, source: Path, folder: Path) -> dict:
    """从隔离目录启动实际 EXE，验证窗口、截图和正常退出。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    started = time.perf_counter()
    screenshot = folder / "exe-startup.png"
    with tempfile.TemporaryDirectory(prefix="exe-isolated-", dir=folder) as directory:
        process = subprocess.Popen([str(executable), str(source)], cwd=directory, env=environment)
        try:
            handle = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"EXE 在显示窗口前退出，退出码：{process.returncode}")
                handle = find_app_window(process_family(process.pid))
                if handle:
                    break
                time.sleep(0.1)
            if handle is None:
                raise RuntimeError("45 秒内未找到 EXE 主窗口")
            startup_seconds = time.perf_counter() - started
            # 留出一次绘制和初始图片解码的时间，仅截取本次启动的主窗口。
            time.sleep(1.2)
            ImageGrab.grab(window=handle).save(screenshot)
            user.PostMessageW(handle, 0x0010, 0, 0)
            exit_code = process.wait(timeout=20)
            if exit_code != 0:
                raise RuntimeError(f"EXE 异常退出，退出码：{exit_code}")
        finally:
            if process.poll() is None:
                # 只清理本验证脚本创建的进程树，避免验证失败后残留窗口。
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                process.wait(timeout=10)
    return dict(
        path=str(executable), size_bytes=executable.stat().st_size,
        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        startup_seconds=round(startup_seconds, 3), exit_code=exit_code,
        isolated_working_directory=True, screenshot=str(screenshot),
        full_mit_notice_embedded=verify_embedded_notice(executable),
    )


def main() -> None:
    """生成本地验证报告；可选参数为需要实际启动验证的 EXE 路径。"""
    parser = argparse.ArgumentParser(description="生成示例、界面截图及单 EXE 启动报告")
    parser.add_argument("executable", nargs="?", type=Path)
    arguments = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("此验证脚本需要 Windows 桌面环境")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    folder = ROOT / "doc" / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    examples = generate_examples(folder)
    screenshot = capture_gui_preview(folder, Path(examples["original"]))
    batch_result = validate_batch(folder)
    batch_screenshot = capture_gui_preview(folder, Path(examples["original"]), batch_mode=True)
    report = dict(examples=examples, gui_preview=str(screenshot), batch_validation=batch_result, batch_preview=str(batch_screenshot))
    if arguments.executable:
        executable = arguments.executable.resolve(strict=True)
        report["executable"] = smoke_executable(executable, Path(examples["original"]), folder)
    output = folder / "release-validation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 只打印公开测试图和成品诊断信息，不记录用户口令或用户图片信息。
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
