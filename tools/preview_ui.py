"""桌面 UI 验证：生成自绘素材，运行真实操作并仅截取本程序窗口。"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import time
import tkinter as tk
from unittest import mock

from PIL import ImageGrab

from confusing_images.gui import ImageToolApp
from tools.validate_release import make_example

ROOT = Path(__file__).resolve().parents[1]


def wait_idle(root: tk.Tk, app: ImageToolApp) -> None:
    """泵送实际窗口消息并限制后台任务等待时间，防止验证卡住。"""
    deadline = time.monotonic() + 30
    while app._busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update()
    if app._busy:
        raise RuntimeError("后台任务超过 30 秒仍未完成")


def capture(root: tk.Tk, app: ImageToolApp, path: Path) -> dict:
    """等待布局稳定后按 HWND 截图，并核对主要操作控件未被挤出父容器。"""
    for _ in range(6):
        root.update()
        time.sleep(0.05)
    widgets = (app.password_entry, app.compatibility_check, app.process_button, app.save_button)
    for widget in widgets:
        node = widget
        while node is not root:
            parent = node.master
            if node.winfo_y() < 0 or node.winfo_y() + node.winfo_height() > parent.winfo_height():
                raise AssertionError(f"控件在垂直方向被截断：{widget}")
            node = parent
    handle = int(str(root.tk.call("wm", "frame", root._w)), 0)
    ImageGrab.grab(window=handle).save(path)
    return dict(file=str(path), window=[root.winfo_width(), root.winfo_height()],
                result_canvas=[app.result_preview.canvas.winfo_width(), app.result_preview.canvas.winfo_height()])


def main() -> None:
    """验证空白、单图、批量、小窗口和大列表，素材全部生成于忽略目录。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    folder = ROOT / "doc" / "validation" / "ui-0.8.0"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "山水示例.png"
    make_example().save(source)
    batch_folder = folder / "批量示例"
    batch_folder.mkdir(exist_ok=True)
    for name in ("山水原图.png", "用于检验长文件名显示和路径查看的山水图片.png", "透明与色彩测试.png"):
        make_example().save(batch_folder / name)
    root = tk.Tk()
    app = ImageToolApp(root)
    report = dict(screenshots=[])
    errors = []
    try:
        with mock.patch("confusing_images.gui.messagebox.askokcancel", return_value=True), \
             mock.patch("confusing_images.gui.messagebox.showinfo"), \
             mock.patch("confusing_images.gui.messagebox.showerror", side_effect=lambda title, message, **kwargs: errors.append(message)):
            report["screenshots"].append(capture(root, app, folder / "01-empty.png"))
            app.load_path(source)
            wait_idle(root, app)
            app.process_image()
            wait_idle(root, app)
            if app.result_image is None:
                raise AssertionError("单图处理没有结果")
            report["screenshots"].append(capture(root, app, folder / "02-single.png"))
            app.load_directory(batch_folder)
            wait_idle(root, app)
            app.file_list.select_all_var.set(True)
            app.file_list._toggle_all()
            app.process_image()
            wait_idle(root, app)
            if app.batch_result is None or app.batch_result.succeeded != 3 or app.batch_result.failed:
                raise AssertionError("批量处理没有成功保存三张图片")
            report["batch_saved"] = app.batch_result.succeeded
            report["screenshots"].append(capture(root, app, folder / "03-batch.png"))
            root.geometry("1120x760")
            report["screenshots"].append(capture(root, app, folder / "04-minimum.png"))
            root.geometry("1280x840")
            app._clear_result()
            paths = [Path("分类素材") / f"图片资源_长文件名用于确认列表横向滚动_{index:05d}.png" for index in range(9403)]
            started = time.perf_counter()
            app.file_list.populate(paths)
            report["list_population_seconds"] = round(time.perf_counter() - started, 3)
            app.file_list.select_all_var.set(True)
            app.file_list._toggle_all()
            app.file_list.tree.selection_set("file-0")
            app.file_list.tree.focus("file-0")
            app.file_list._show_path()
            started = time.perf_counter()
            app.file_list._toggle_item("file-0")
            report["single_toggle_seconds"] = round(time.perf_counter() - started, 4)
            app.file_list.set_placeholder("9,403 条虚拟路径 · 仅验证列表显示与交互")
            app.status.set("大列表界面验证 · 虚拟路径仅在内存中，不读写实际素材")
            report["screenshots"].append(capture(root, app, folder / "05-large-list.png"))
            if errors:
                raise AssertionError("；".join(errors))
    finally:
        app.result_image = None
        app._close()
    (folder / "ui-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
