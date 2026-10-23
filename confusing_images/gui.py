"""Tkinter 中文桌面界面：后台处理、双图预览和安全保存。"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from . import __version__
from .branding import create_icon
from .batch import BatchPlan, BatchResult, collect_images, run_batch
from .core import ALGORITHM, ImageToolError, METADATA_KEY, RESTORE_NOTE, RESTORE_STATUS, image_algorithm, image_requires_password, load_image, manual_restore_description, process_compatible, restore_selected_image, save_png, scramble_image
from .upstream import ALGORITHMS

BACKGROUND = "#F3F6FA"
SURFACE = "#FFFFFF"
INK = "#172D49"
MUTED = "#6C7D93"
ACCENT = "#2563EB"
FONT = "Microsoft YaHei UI"
DEFAULT_ALGORITHM = "tomato"
ALL_ALGORITHMS = ALGORITHMS
ALGORITHM_LABELS = dict((item[0], item[1]) for item in ALL_ALGORITHMS)
ALGORITHM_LABELS[ALGORITHM] = "旧版图片（仅还原）"
NATIVE_NOTICE = "请保留完整 PNG，勿压缩、裁切或删除参数。无口令图片任何持有人均可还原；视觉混淆不是安全加密。"
COMPAT_NOTICE = "原项目兼容模式：没有像素或错误密钥校验；Sort/Random 末行有损。仅支持原项目默认参数及完整 PNG。"


def batch_worker_hint() -> str:
    """返回批量并行线程数说明，供确认对话框展示。"""
    count = max(1, min(32, (os.cpu_count() or 1)))
    return f"最多 {count} 个并行线程（按 CPU 核数）"


class FileListPanel(tk.Frame):
    """目录模式下的可勾选文件列表，支持全选/全不选和逐行切换。"""

    def __init__(self, parent: tk.Misc, title: str, on_change: Callable | None = None) -> None:
        """创建列表、全选按钮和统计，变化时通知外部刷新控件。"""
        super().__init__(parent, bg=SURFACE, highlightbackground="#DFE6F0", highlightthickness=1)
        self.on_change = on_change
        self._checked: set[str] = set()
        self._files: dict[str, Path] = dict()
        self.title = tk.Label(self, text=title, bg=SURFACE, fg=INK, font=(FONT, 11, "bold"))
        self.title.pack(anchor="w", padx=18, pady=(14, 8))
        toolbar = tk.Frame(self, bg=SURFACE)
        toolbar.pack(fill="x", padx=18, pady=(0, 8))
        self.select_all_var = tk.BooleanVar(value=False)
        self.select_all_check = ttk.Checkbutton(
            toolbar, text="全选 / 全不选", variable=self.select_all_var, command=self._toggle_all,
        )
        self.select_all_check.pack(side="left")
        self.counter = tk.Label(toolbar, text="已选 0 / 共 0", bg=SURFACE, fg=MUTED, font=(FONT, 9))
        self.counter.pack(side="right")
        body = tk.Frame(self, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.tree = ttk.Treeview(body, columns=("check", "name", "rel"), show="headings", selectmode="browse")
        self.tree.heading("check", text="")
        self.tree.heading("name", text="文件名")
        self.tree.heading("rel", text="相对路径")
        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("rel", width=320, anchor="w")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.detail = tk.Label(self, text="等待载入", bg=SURFACE, fg=MUTED, font=(FONT, 9), anchor="w")
        self.detail.pack(side="bottom", fill="x", padx=18, pady=12)

    def populate(self, files: list[Path]) -> None:
        """清空并按相对路径填充列表，默认全部不勾选。"""
        self._checked.clear()
        self._files.clear()
        for child in self.tree.get_children():
            self.tree.delete(child)
        for index, relative in enumerate(files):
            iid = f"file-{index}"
            self._files[iid] = relative
            self.tree.insert("", "end", iid=iid, values=("☐", relative.name, str(relative.parent) if str(relative.parent) != "." else "（根目录）"))
        self._refresh()

    def set_placeholder(self, text: str) -> None:
        """列表为空时在标题下提示，不清除已有数据。"""
        self.detail.configure(text=text)

    def checked_files(self) -> list[Path]:
        """按原相对顺序返回勾选的文件相对路径。"""
        return [self._files[iid] for iid in self.tree.get_children() if iid in self._checked]

    def _toggle_all(self) -> None:
        """全选或全不选，并保持列表原顺序。"""
        if self.select_all_var.get():
            self._checked = set(self._files.keys())
        else:
            self._checked.clear()
        self._refresh()

    def _on_click(self, event: tk.Event) -> None:
        """点击行首切换勾选状态，点击其它列不处理。"""
        region = self.tree.identify("region", event.x, event.y)
        iid = self.tree.identify_row(event.y)
        if iid and region == "cell":
            column = self.tree.identify_column(event.x)
            if column == "#1":
                if iid in self._checked:
                    self._checked.discard(iid)
                else:
                    self._checked.add(iid)
                self._refresh()

    def _refresh(self) -> None:
        """更新勾选标记、统计和全选状态，并通知外部。"""
        total = len(self._files)
        for iid in self._files:
            mark = "☑" if iid in self._checked else "☐"
            values = self.tree.item(iid, "values")
            self.tree.item(iid, values=(mark, values[1], values[2]))
        self.counter.configure(text=f"已选 {len(self._checked)} / 共 {total}")
        self.select_all_var.set(total > 0 and len(self._checked) == total)
        if self.on_change is not None:
            self.on_change()


class PreviewPanel(tk.Frame):
    """显示自适应图片预览、透明棋盘背景及图片摘要。"""

    def __init__(self, parent: tk.Misc, title: str, empty_text: str) -> None:
        """创建独立的预览卡片并监听尺寸变化。"""
        super().__init__(parent, bg=SURFACE, highlightbackground="#DFE6F0", highlightthickness=1)
        self.image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._resize_id: str | None = None
        self._empty_text = empty_text
        self.title = tk.Label(self, text=title, bg=SURFACE, fg=INK, font=(FONT, 11, "bold"))
        self.title.pack(anchor="w", padx=18, pady=(14, 10))
        self.canvas = tk.Canvas(self, bg="#EDF2F8", highlightthickness=0, width=400, height=300)
        self.detail = tk.Label(self, text="等待载入", bg=SURFACE, fg=MUTED, font=(FONT, 9), anchor="w")
        self.detail.pack(side="bottom", fill="x", padx=18, pady=12)
        self.canvas.pack(fill="both", expand=True, padx=12)
        self.canvas.bind("<Configure>", self._schedule_render)

    def set_image(self, image: Image.Image | None, detail: str = "等待载入") -> None:
        """更新预览来源，不缩放或修改实际处理的图片。"""
        self.image = image
        self.detail.configure(text=detail)
        self._render()

    def set_placeholder(self, text: str, detail: str = "等待处理") -> None:
        """在目录模式下显示统计说明，而不把所有图片载入内存。"""
        self._empty_text = text
        self.set_image(None, detail)

    def _schedule_render(self, event: tk.Event | None = None) -> None:
        """合并连续窗口缩放事件，避免反复缩放大图。"""
        if self._resize_id is not None:
            self.after_cancel(self._resize_id)
        self._resize_id = self.after(70, self._render)

    def _render(self) -> None:
        """将图片等比缩略到画布，RGBA 图片使用棋盘背景。"""
        if self._resize_id is not None:
            self.after_cancel(self._resize_id)
            self._resize_id = None
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        if self.image is None:
            self._photo = None
            center_x, center_y = width / 2, height / 2
            self.canvas.create_rectangle(
                center_x - 30, center_y - 64, center_x + 30, center_y - 12,
                outline="#AABBD1", width=2, dash=(5, 4),
            )
            self.canvas.create_line(
                center_x - 20, center_y - 24, center_x - 4, center_y - 44,
                center_x + 8, center_y - 32, center_x + 20, center_y - 47,
                fill="#AABBD1", width=2,
            )
            self.canvas.create_text(
                center_x, center_y + 20, text=self._empty_text,
                fill=MUTED, font=(FONT, 10), justify="center", width=max(100, width - 40),
            )
            return
        preview = self.image.copy()
        preview.thumbnail((max(1, width - 24), max(1, height - 24)), Image.Resampling.LANCZOS)
        if preview.mode == "RGBA":
            checker = Image.new("RGB", preview.size, "#FFFFFF")
            draw = ImageDraw.Draw(checker)
            for top in range(0, preview.height, 12):
                for left in range(0, preview.width, 12):
                    if (top // 12 + left // 12) % 2:
                        draw.rectangle((left, top, left + 11, top + 11), fill="#E1E7EF")
            checker.paste(preview, mask=preview.getchannel("A"))
            preview = checker
        self._photo = ImageTk.PhotoImage(preview, master=self)
        self.canvas.create_image(width / 2, height / 2, image=self._photo)


class ImageToolApp(ttk.Frame):
    """管理处理状态，确保所有 Tk 调用均运行于主线程。"""

    def __init__(self, root: tk.Tk) -> None:
        """初始化应用状态、界面和后台消息轮询。"""
        super().__init__(root)
        self.root = root
        self.input_image: Image.Image | None = None
        self.result_image: Image.Image | None = None
        self.input_path: Path | None = None
        self.batch_plan: BatchPlan | None = None
        self.batch_result: BatchResult | None = None
        self._batch_running = False
        self._cancel_batch = threading.Event()
        self._suggested_stem = "image"
        self._result_mode = "scramble"
        self._result_compatibility = False
        self._busy = False
        self._closed = False
        self._events: queue.Queue = queue.Queue()
        self._on_success: Callable | None = None
        self.mode = tk.StringVar(value="scramble")
        self.password = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)
        self.compatibility = tk.BooleanVar(value=False)
        self.algorithm_name = tk.StringVar(value=ALGORITHM_LABELS[DEFAULT_ALGORITHM])
        self.algorithm_hint = tk.StringVar()
        self.password_caption = tk.StringVar(value="口令（可选，留空即可处理）")
        self.format_notice = tk.StringVar(value=NATIVE_NOTICE)
        self.file_text = tk.StringVar(value="选择 Pillow 可解码的静态图片；不限制来源、扩展名或固定尺寸")
        self.status = tk.StringVar(value="准备就绪 · 图片仅在本机处理，不会上传")
        self.progress = tk.DoubleVar(value=0)
        self._configure_window()
        self._build_interface()
        self._update_algorithm_hint()
        self.password.trace_add("write", self._password_changed)
        self._poll_id = self.root.after(70, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Control-o>", lambda event: self.choose_image())
        self.root.bind("<Control-Return>", lambda event: self.process_image())

    def _configure_window(self) -> None:
        """设置窗口和统一的轻量浅色主题。"""
        self.root.title(f"混淆图工具 · Confusing Images {__version__}")
        self.root.geometry("1200x860")
        self.root.minsize(1050, 780)
        self.root.configure(bg=BACKGROUND)
        self._window_icon = ImageTk.PhotoImage(create_icon(), master=self.root)
        self.root.iconphoto(True, self._window_icon)
        self.root.option_add("*Font", (FONT, 10))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TButton", font=(FONT, 10), padding=(14, 9), background=SURFACE, foreground=INK)
        style.map("TButton", background=[("active", "#E8EFFB"), ("disabled", "#EEF1F5")])
        style.configure("Primary.TButton", background=ACCENT, foreground="white", borderwidth=0)
        style.map(
            "Primary.TButton",
            background=[("disabled", "#A7BCE9"), ("active", "#1D4ED8")],
            foreground=[("disabled", "#F7F9FC")],
        )
        style.configure("TRadiobutton", background=SURFACE, foreground=INK, padding=(4, 6))
        style.configure("TCheckbutton", background=SURFACE, foreground=MUTED)
        style.configure("TEntry", padding=8, fieldbackground=SURFACE)
        style.configure("TCombobox", padding=8, fieldbackground=SURFACE)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#E3EAF4", borderwidth=0)

    def _build_interface(self) -> None:
        """构建标题、处理选项、双图预览和底部操作区。"""
        outer = tk.Frame(self.root, bg=BACKGROUND)
        outer.pack(fill="both", expand=True, padx=26, pady=22)
        # 修复小窗口底部被截断的问题：仅预览行伸缩，状态和警告始终保留。
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        header = tk.Frame(outer, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        tk.Label(
            header, text="CI", bg=ACCENT, fg="white", font=(FONT, 18, "bold"), padx=12, pady=7,
        ).pack(side="left", padx=(0, 14))
        titles = tk.Frame(header, bg=BACKGROUND)
        titles.pack(side="left")
        tk.Label(titles, text="混淆图工具", bg=BACKGROUND, fg=INK, font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(titles, text="打散图块，保留找回原图的方式。", bg=BACKGROUND, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        tk.Label(
            header, text="本地处理  /  像素级还原", bg="#E5EDFF", fg="#285CB8", padx=12, pady=7,
        ).pack(side="right")

        options = tk.Frame(outer, bg=SURFACE, highlightbackground="#DFE6F0", highlightthickness=1)
        options.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        file_row = tk.Frame(options, bg=SURFACE)
        file_row.pack(fill="x", padx=18, pady=(16, 10))
        self.open_button = ttk.Button(file_row, text="选择图片…", command=self.choose_image)
        self.open_button.pack(side="left", padx=(0, 12))
        self.folder_button = ttk.Button(file_row, text="选择文件夹…", command=self.choose_directory)
        self.folder_button.pack(side="left", padx=(0, 12))
        ttk.Entry(file_row, textvariable=self.file_text, state="readonly").pack(side="left", fill="x", expand=True)
        mode_row = tk.Frame(options, bg=SURFACE)
        mode_row.pack(fill="x", padx=18, pady=(0, 8))
        self.scramble_radio = ttk.Radiobutton(
            mode_row, text="混淆图片", variable=self.mode, value="scramble", command=self._mode_changed,
        )
        self.scramble_radio.pack(side="left", padx=(0, 18))
        self.restore_radio = ttk.Radiobutton(
            mode_row, text="还原图片", variable=self.mode, value="restore", command=self._mode_changed,
        )
        self.restore_radio.pack(side="left")
        self.compatibility_check = ttk.Checkbutton(
            mode_row, text="强制手动尝试（忽略嵌入参数）", variable=self.compatibility, command=self._format_changed,
        )
        self.compatibility_check.pack(side="right")
        fields = tk.Frame(options, bg=SURFACE)
        fields.pack(fill="x", padx=18, pady=(0, 16))
        fields.columnconfigure(0, weight=3)
        fields.columnconfigure(2, weight=2)
        tk.Label(fields, textvariable=self.password_caption, bg=SURFACE, fg=INK, anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 5))
        tk.Label(fields, text="混淆算法", bg=SURFACE, fg=INK).grid(row=0, column=2, sticky="w", pady=(0, 5))
        self.password_entry = ttk.Entry(fields, textvariable=self.password, show="●")
        self.password_entry.grid(row=1, column=0, sticky="ew")
        self.show_check = ttk.Checkbutton(
            fields, text="显示", variable=self.show_password, command=self._toggle_password,
        )
        self.show_check.grid(row=1, column=1, padx=(10, 24))
        self.algorithm_box = ttk.Combobox(
            fields, textvariable=self.algorithm_name, values=[item[1] for item in ALL_ALGORITHMS], state="readonly", width=27,
        )
        self.algorithm_box.grid(row=1, column=2, sticky="ew")
        self.algorithm_box.bind("<<ComboboxSelected>>", self._algorithm_changed)
        tk.Label(fields, textvariable=self.algorithm_hint, bg=SURFACE, fg=MUTED, anchor="w", font=(FONT, 9), wraplength=1080).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(9, 0),
        )

        previews = tk.Frame(outer, bg=BACKGROUND)
        previews.grid(row=2, column=0, sticky="nsew")
        previews.columnconfigure(0, weight=1, uniform="preview")
        previews.columnconfigure(1, weight=1, uniform="preview")
        previews.rowconfigure(0, weight=1)
        left = tk.Frame(previews, bg=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.input_preview = PreviewPanel(left, "01 / 输入图片", "尚未选择图片\n点击上方“选择图片”开始")
        self.input_preview.grid(row=0, column=0, sticky="nsew")
        self.file_list = FileListPanel(left, "01 / 图片列表", self._selection_changed)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        self.file_list.grid_remove()
        self.result_preview = PreviewPanel(previews, "02 / 混淆结果", "处理后的图片将在这里显示\n预览缩放不影响实际输出尺寸")
        self.result_preview.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        footer = tk.Frame(outer, bg=BACKGROUND)
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.batch_details_button = ttk.Button(footer, text="批量详情", command=self.show_batch_details, state="disabled")
        self.batch_details_button.pack(side="left")
        self.save_button = ttk.Button(footer, text="保存 PNG…", command=self.save_result, state="disabled")
        self.save_button.pack(side="right", padx=(10, 0))
        self.process_button = ttk.Button(footer, text="开始混淆", style="Primary.TButton", command=self.process_image, state="disabled")
        self.process_button.pack(side="right")
        self.stop_button = ttk.Button(footer, text="停止批量", command=self.stop_batch, state="disabled")
        self.stop_button.pack(side="right", padx=(0, 10))
        ttk.Progressbar(outer, variable=self.progress, maximum=100).grid(row=4, column=0, sticky="ew", pady=(14, 8))
        tk.Label(outer, textvariable=self.status, bg=BACKGROUND, fg=MUTED, anchor="w", font=(FONT, 9)).grid(row=5, column=0, sticky="ew")
        tk.Label(
            outer,
            textvariable=self.format_notice,
            bg="#FFF3DA", fg="#93641F", padx=12, pady=9, anchor="w", font=(FONT, 9),
            wraplength=1080,
        ).grid(row=6, column=0, sticky="ew", pady=(10, 0))

    def _algorithm_id(self) -> str:
        """把显示名称转换为稳定算法标识，不依赖下拉框的数字下标。"""
        for identifier, label, hint in ALL_ALGORITHMS:
            if self.algorithm_name.get() == label:
                return identifier
        if self.algorithm_name.get() == ALGORITHM_LABELS[ALGORITHM]:
            return ALGORITHM
        return DEFAULT_ALGORITHM

    def _key_optional(self) -> bool:
        """新混淆允许空口令；旧图片和原项目算法仍保留自己的密钥要求。"""
        algorithm = self._algorithm_id()
        if self.compatibility.get():
            if self.mode.get() == "restore":
                return True
            return algorithm == "sort" or (algorithm == "random" and self.mode.get() == "restore")
        if self.mode.get() == "restore" and self.batch_plan is None and self.input_image is not None and METADATA_KEY in self.input_image.info:
            try:
                return not image_requires_password(self.input_image)
            except ImageToolError:
                pass
        return True

    def _key_disabled(self) -> bool:
        """只对无需输入密钥的还原或原项目操作禁用输入框，不禁止自选口令。"""
        if self.compatibility.get():
            algorithm = self._algorithm_id()
            return algorithm == "sort" or (algorithm == "random" and self.mode.get() == "restore")
        return self.mode.get() == "restore" and self.batch_plan is None and self.input_image is not None and METADATA_KEY in self.input_image.info and self._key_optional()

    def _update_algorithm_hint(self) -> None:
        """按模式显示密钥约束或本工具派生口令规则。"""
        algorithm = self._algorithm_id()
        if self.mode.get() == "restore" and (self.compatibility.get() or (self.batch_plan is None and (self.input_image is None or METADATA_KEY not in self.input_image.info))):
            self.password_caption.set("手动密钥（留空使用显示的默认参数）")
            try:
                self.algorithm_hint.set(manual_restore_description(algorithm, self.password.get()))
            except ImageToolError as error:
                self.algorithm_hint.set(str(error))
        elif self.compatibility.get():
            self.algorithm_hint.set(next(item[2] for item in ALL_ALGORITHMS if item[0] == algorithm))
            self.password_caption.set("原项目密钥（按算法要求填写）")
        elif self.mode.get() == "restore" and self.batch_plan is not None:
            self.password_caption.set("口令（用于批量中的有口令图片）")
            try:
                manual_hint = manual_restore_description(algorithm, self.password.get())
            except ImageToolError as error:
                manual_hint = str(error)
            self.algorithm_hint.set("带参数图片自动校验；无参数图片按所选算法尝试。" + manual_hint)
        elif self.mode.get() == "restore" and self.input_image is not None and METADATA_KEY in self.input_image.info:
            if self._key_optional():
                self.password_caption.set("无需口令（图片自带公开参数）")
                self.algorithm_hint.set("这张图片无需口令，任何持有完整 PNG 的人均可还原。")
            else:
                self.password_caption.set("口令（此图片需要原口令）")
                self.algorithm_hint.set("这张图片设置了口令，请输入生成时使用的原口令；旧版图片也保留此要求。")
        else:
            self.password_caption.set("口令（可选，留空即可处理）")
            self.algorithm_hint.set("留空：参数随 PNG 保存，无需口令即可还原；填写：还原需使用相同口令。请保留完整 PNG。")
        self.format_notice.set(COMPAT_NOTICE if self.compatibility.get() else NATIVE_NOTICE)

    def _algorithm_changed(self, event: tk.Event | None = None) -> None:
        """清除旧结果；旧算法仅用于识别和还原，不再提供新的混淆入口。"""
        if self._algorithm_id() == ALGORITHM and (self.compatibility.get() or self.mode.get() != "restore"):
            self.algorithm_name.set(ALGORITHM_LABELS[DEFAULT_ALGORITHM])
        self._update_algorithm_hint()
        self._clear_result()

    def _format_changed(self) -> None:
        """允许用户显式强制按原格式尝试，并用未校验状态区分结果。"""
        self._algorithm_changed()

    def _toggle_password(self) -> None:
        """仅切换口令的显示形式，不改变实际输入。"""
        self.password_entry.configure(show="" if self.show_password.get() else "●")

    def _password_changed(self, *arguments: str) -> None:
        """修改口令后丢弃旧结果，避免误认为旧图使用了新口令。"""
        if not self._busy and self.result_image is not None:
            self._clear_result()
            self.status.set("口令已修改 · 请重新处理图片")
        if not self._busy:
            self._update_algorithm_hint()

    def _set_controls(self) -> None:
        """统一更新可交互状态，处理期间禁止修改输入和重复启动。"""
        state = "disabled" if self._busy else "normal"
        for widget in (self.open_button, self.folder_button, self.scramble_radio, self.restore_radio, self.compatibility_check):
            widget.configure(state=state)
        key_state = "disabled" if self._busy or self._key_disabled() else "normal"
        self.password_entry.configure(state=key_state)
        self.show_check.configure(state=key_state)
        restoring = self.mode.get() == "restore"
        auto_algorithm = restoring and not self.compatibility.get() and (
            self.batch_plan is None and self.input_image is not None and METADATA_KEY in self.input_image.info
        )
        self.algorithm_box.configure(state="disabled" if self._busy or auto_algorithm else "readonly")
        has_input = bool(self.file_list.checked_files()) if self.batch_plan is not None else self.input_image is not None
        self.process_button.configure(state="normal" if not self._busy and has_input else "disabled")
        result_state = "normal" if not self._busy and self.result_image is not None else "disabled"
        self.save_button.configure(state=result_state)
        self.batch_details_button.configure(state="normal" if not self._busy and self.batch_result is not None else "disabled")
        can_stop = self._busy and self._batch_running and not self._cancel_batch.is_set()
        self.stop_button.configure(state="normal" if can_stop else "disabled")
        # 目录模式下全选按钮随忙碌状态禁用，避免处理中改选。
        if hasattr(self, "file_list"):
            self.file_list.select_all_check.configure(state="disabled" if self._busy else "normal")

    def _clear_result(self) -> None:
        """清除过期结果，避免参数变化后误保存旧图片。"""
        self.result_image = None
        self.result_preview.set_image(None)
        self.progress.set(0)
        self._set_controls()

    def _selection_changed(self) -> None:
        """勾选状态变化后刷新控件和计数提示。"""
        if not self._busy:
            self._set_controls()

    def _show_file_list(self) -> None:
        """目录模式显示可勾选列表，隐藏单图预览。"""
        self.input_preview.grid_remove()
        self.file_list.grid()

    def _show_single_preview(self) -> None:
        """单图模式显示预览，隐藏文件列表。"""
        self.file_list.grid_remove()
        self.input_preview.grid()

    def _mode_changed(self) -> None:
        """切换操作文案并清除上一次模式的结果。"""
        restoring = self.mode.get() == "restore"
        if not restoring and self._algorithm_id() == ALGORITHM:
            self.algorithm_name.set(ALGORITHM_LABELS[DEFAULT_ALGORITHM])
        verb = "还原" if restoring else "混淆"
        prefix = "批量" if self.batch_plan is not None else ""
        self.process_button.configure(text=f"开始{prefix}{verb}")
        self.result_preview.title.configure(text=f"02 / {prefix}{verb}结果")
        self.file_list.title.configure(text=f"01 / 图片列表 · {verb}")
        self._update_algorithm_hint()
        self._clear_result()

    def choose_image(self) -> None:
        """打开系统文件选择框，不自动保存或覆盖任何图片。"""
        if self._busy:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择需要混淆或还原的图片",
            filetypes=[("所有文件（自动识别图片格式）", "*")],
        )
        if path:
            self.load_path(Path(path))

    def choose_directory(self) -> None:
        """选择批量根目录，取消选择时不改变现有输入。"""
        if self._busy:
            return
        directory = filedialog.askdirectory(parent=self.root, title="选择图片文件夹（包含子目录）", mustexist=True)
        if directory:
            self.load_directory(Path(directory))

    def load_directory(self, directory: Path) -> None:
        """在后台扫描目录快照；扫描成功后才切换到批量模式。"""
        if self._busy:
            return

        def action(progress: Callable) -> BatchPlan:
            """只读遍历图片，不在选择阶段创建或保存任何文件。"""
            return collect_images(directory, progress)

        def complete(plan: BatchPlan) -> None:
            """展示文件列表，沿用用户选择的操作方向与参数。"""
            self.batch_plan = plan
            self.input_image = None
            self.input_path = None
            self.file_text.set(str(plan.source_directory))
            self._mode_changed()
            self.file_list.populate(list(plan.files))
            self.file_list.set_placeholder(f"递归扫描 / 跳过 {plan.linked_entries_skipped} 个链接 / {len(plan.scan_errors)} 项扫描问题")
            self._show_file_list()
            self.result_preview.set_placeholder("勾选图片后开始处理\n结果将自动保存为 PNG，保留层级、重名编号", "输出：所选目录 / confusing_images")
            self.status.set(f"目录已载入 · 共 {len(plan.files)} 张图片 · 请在列表中勾选需要处理的图片")
            self._set_controls()
            if not plan.files:
                messagebox.showinfo("没有可处理图片", "未找到 Pillow 解码器可读取的图片。输出目录和链接会自动排除。", parent=self.root)

        self._run_task("正在扫描目录…", action, complete)

    def process_directory(self) -> None:
        """确认输出路径和当前参数后，对勾选的图片启动后台批量处理并自动保存。"""
        plan = self.batch_plan
        if self._busy or plan is None or not plan.files:
            return
        selected = self.file_list.checked_files()
        if not selected:
            messagebox.showwarning("未选择图片", "请在左侧列表中勾选需要处理的图片，或使用“全选”。", parent=self.root)
            return
        password = self.password.get()
        if not password and not self._key_optional():
            messagebox.showwarning("需要口令", "请输入本次批量处理使用的口令。", parent=self.root)
            self.password_entry.focus_set()
            return
        inverse = self.mode.get() == "restore"
        compatible = self.compatibility.get()
        algorithm = self._algorithm_id()
        verb = "还原" if inverse else "混淆"
        algorithm_text = f"有参数自动识别；无参数使用 {ALGORITHM_LABELS[algorithm]}" if inverse and not compatible else ALGORITHM_LABELS[algorithm]
        # 仅处理勾选的子集，保持相对层级与输出目录约定不变。
        from dataclasses import replace
        subset_plan = replace(plan, files=tuple(selected))
        confirmation = (
            f"将批量{verb} {len(selected)} 张图片（已从 {len(plan.files)} 张中勾选）。\n算法：{algorithm_text}\n"
            f"输入：{plan.source_directory}\n输出：{plan.output_directory}\n\n"
            "保留目录层级，重名自动编号，不覆盖原图或旧结果。\n"
            f"多线程并行：{batch_worker_hint()}。"
        )
        if compatible:
            confirmation += "\n\n原项目兼容模式没有错误密钥或像素校验。"
            if algorithm in ("sort", "random"):
                confirmation += "Sort/Random 原格式末行有损，无法完整恢复。"
        elif inverse:
            confirmation += "\n\n有内嵌参数且未勾选强制手动时，按参数自动还原和校验。无参数图片或勾选强制手动时按当前算法尝试，结果不校验。"
            try:
                confirmation += "\n无参数图片：" + manual_restore_description(algorithm, password)
            except ImageToolError as error:
                confirmation += "\n当前手动密钥参数无效：" + str(error)
        elif not password:
            confirmation += "\n\n本次未设置口令：任何持有完整输出 PNG 的人都能还原。"
        if not messagebox.askokcancel("确认批量处理", confirmation, parent=self.root):
            return
        self._clear_result()
        self._cancel_batch.clear()
        self._batch_running = True

        def action(progress: Callable) -> BatchResult:
            """逐张处理并写入输出树，工作线程不持有 Tk 控件。"""
            return run_batch(
                subset_plan, password, algorithm=algorithm, inverse=inverse,
                compatible=compatible, progress=progress, cancel_event=self._cancel_batch,
            )

        def complete(result: BatchResult) -> None:
            """显示成功、失败、未处理及输出位置，不要求再次手动保存。"""
            self.batch_result = result
            self.progress.set(100 * result.completed / max(1, result.total))
            state = "已停止" if result.cancelled else "已完成"
            summary = f"批量{state} · 已保存 {result.succeeded}（未校验 {result.unverified}）· 失败 {result.failed} · 未处理 {result.total - result.completed}"
            self.result_preview.set_placeholder(
                f"批量{state}\n已保存 {result.succeeded} 张 / 失败 {result.failed} 张\n其中 {result.unverified} 张为未校验尝试结果",
                "自动保存 / 已保存不等于正确还原",
            )
            self.status.set(summary + f" · 输出：{result.output_directory}")
            self._set_controls()
            messagebox.showinfo("批量处理结果", summary + f"\n\n输出目录：\n{result.output_directory}\n\n可点击“批量详情”查看失败原因和文件对应关系。", parent=self.root)

        self._run_task(f"正在批量{verb}…", action, complete)

    def stop_batch(self) -> None:
        """请求在当前图片完成后停止，已保存的结果不会删除。"""
        if self._busy and self._batch_running:
            self._cancel_batch.set()
            self.stop_button.configure(state="disabled")
            self.status.set("正在停止批量处理 · 将完成当前图片保存后停止")

    def show_batch_details(self) -> None:
        """展示上一次批量统计和路径映射，全部内容仅留在当前程序内存中。"""
        result = self.batch_result
        if result is None:
            return
        window = tk.Toplevel(self.root)
        window.title("批量处理详情")
        window.geometry("850x580")
        text = tk.Text(window, wrap="word", padx=16, pady=16, font=(FONT, 10))
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        lines = [
            f"输入：{result.source_directory}", f"输出：{result.output_directory}",
            f"共 {result.total} 张 / 成功 {result.succeeded} / 失败 {result.failed} / 未处理 {result.total - result.completed}",
            f"其中未校验尝试结果 {result.unverified} 张，不代表已正确恢复原图。",
            f"跳过链接：{result.linked_entries_skipped} / 扫描问题：{len(result.scan_errors)}", "", "失败及扫描问题：",
        ]
        lines.extend(result.errors + list(result.scan_errors) or ["无"])
        lines.extend(["", "未校验尝试说明："] + result.unverified_notes)
        lines.extend(["", "成功文件对应关系（相对路径）："])
        lines.extend(f"{source}  →  {output}" for source, output in result.outputs)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

    def load_path(self, path: Path) -> None:
        """在后台解码指定图片，只有读取成功才替换当前输入。"""
        if self._busy:
            return

        def action(progress: Callable) -> Image.Image:
            """在工作线程读取图片，避免大图解码阻塞界面。"""
            return load_image(path)

        def complete(image: Image.Image) -> None:
            """在主线程接收读取结果，并识别带还原参数的 PNG。"""
            self.batch_plan = None
            self._show_single_preview()
            self.input_preview.title.configure(text="01 / 输入图片")
            self.result_preview.set_placeholder("处理后的图片将在这里显示\n预览缩放不影响实际输出尺寸")
            self.input_path = path
            self._suggested_stem = path.stem
            self.file_text.set(str(path))
            self._set_input(image)
            if self.compatibility.get():
                self.status.set("兼容图片已载入 · 请手动选择正确算法、操作方向和原项目密钥")
            else:
                if METADATA_KEY in image.info:
                    self.status.set("图片已载入 · 无需口令即可还原" if self._key_optional() else "图片已载入 · 输入原口令后还原")
                else:
                    self.status.set("图片已载入 · 无还原参数，按所选算法尝试（未校验）" if self.mode.get() == "restore" else "图片已载入 · 可选择混淆或手动还原")

        self._run_task("正在读取图片…", action, complete)

    def _set_input(self, image: Image.Image) -> None:
        """载入内存图片并根据参数存在与否选择处理模式。"""
        self.input_image = image
        if METADATA_KEY in image.info:
            self.compatibility.set(False)
            self.mode.set("restore")
            try:
                self.algorithm_name.set(ALGORITHM_LABELS[image_algorithm(image)])
            except ImageToolError:
                pass
        elif self._algorithm_id() == ALGORITHM:
            self.algorithm_name.set(ALGORITHM_LABELS[DEFAULT_ALGORITHM])
        self._mode_changed()
        detail = f"{image.width:,} × {image.height:,} px  /  {image.mode}"
        if METADATA_KEY in image.info:
            detail += "  /  含还原参数"
        self.input_preview.set_image(image, detail)
        self._set_controls()

    def process_image(self) -> None:
        """校验输入并启动所选操作，口令不会写入文件或日志。"""
        if self._busy:
            return
        if self.batch_plan is not None:
            self.process_directory()
            return
        if self.input_image is None:
            return
        password = self.password.get()
        if not password and not self._key_optional():
            messagebox.showwarning("需要口令", "请输入口令，并牢记它；程序无法找回口令。", parent=self.root)
            self.password_entry.focus_set()
            return
        image = self.input_image
        mode = self.mode.get()
        algorithm = self._algorithm_id()
        compatible = self.compatibility.get()
        if compatible and algorithm in ("sort", "random") and not messagebox.askokcancel(
            "原项目格式有损提醒",
            "原 Java 格式会覆盖末行、压缩颜色并丢失末行透明度，不能无损找回；还原也不验证密钥。\n仍要按该格式处理吗？",
            parent=self.root,
        ):
            return
        self._clear_result()

        def action(progress: Callable) -> Image.Image:
            """在后台执行混淆或还原，主线程仅负责显示进度。"""
            if mode == "restore":
                return restore_selected_image(image, password, algorithm=algorithm, manual=compatible, progress=progress)
            if compatible:
                return process_compatible(image, password, algorithm, mode == "restore", progress)
            return scramble_image(image, password, progress=progress, algorithm=algorithm)

        def complete(result: Image.Image) -> None:
            """显示完成结果，并启用显式保存操作。"""
            self.result_image = result
            self._result_mode = mode
            unverified = result.info.get(RESTORE_STATUS) == "unverified"
            self._result_compatibility = compatible or unverified
            detail = f"{result.width:,} × {result.height:,} px  /  {result.mode}"
            if compatible or unverified:
                detail += "  /  原格式兼容 · 未校验"
            else:
                detail += "  /  像素校验通过" if mode == "restore" else "  /  PNG 内含还原参数"
            self.result_preview.set_image(result, detail)
            if unverified:
                self.status.set("手动尝试完成 · " + result.info.get(RESTORE_NOTE, "结果未校验"))
            elif compatible:
                self.status.set("原格式兼容处理完成 · 未验证原图或密钥，请自行检查结果并保存 PNG")
            else:
                if mode == "restore":
                    self.status.set("还原完成，像素校验通过 · 请保存结果")
                else:
                    self.status.set("混淆完成 · 请保存 PNG 并牢记口令" if password else "混淆完成 · 无口令模式，持有完整 PNG 即可还原")
            self._set_controls()

        self._run_task("正在处理…", action, complete)

    def save_result(self) -> None:
        """选择 PNG 目标路径，禁止覆盖当前磁盘输入文件。"""
        if self._busy or self.result_image is None:
            return
        suffix = "_restored" if self._result_mode == "restore" else "_scrambled"
        if self._result_compatibility:
            suffix = "_compat" + suffix
        path = filedialog.asksaveasfilename(
            parent=self.root, title="保存结果为 PNG", defaultextension=".png",
            initialfile=self._suggested_stem + suffix + ".png",
            filetypes=[("无损 PNG 图片", "*.png")], confirmoverwrite=True,
        )
        if not path:
            return
        output = Path(path)
        if self.input_path is not None and output.resolve() == self.input_path.resolve():
            messagebox.showwarning("保护输入文件", "请使用其他文件名，不能直接覆盖输入图片。", parent=self.root)
            return
        image = self.result_image

        def action(progress: Callable) -> Path:
            """在后台原子写入 PNG，失败时保留已有目标文件。"""
            return save_png(image, output)

        def complete(saved: Path) -> None:
            """显示实际保存路径，不自动打开额外程序。"""
            self.status.set(f"已保存：{saved}")

        self._run_task("正在保存 PNG…", action, complete)

    def _run_task(self, text: str, action: Callable, on_success: Callable) -> None:
        """启动单个后台任务，通过队列安全传递进度、结果和错误。"""
        self._busy = True
        self._on_success = on_success
        self.status.set(text)
        self.progress.set(0)
        self._set_controls()

        def report(value: float, message: str) -> None:
            """将后台进度写入消息队列，不调用任何 Tk 方法。"""
            self._events.put(("progress", (value, message)))

        def worker() -> None:
            """运行处理逻辑并把可显示的错误传回界面。"""
            try:
                result = action(report)
            except MemoryError:
                self._events.put(("error", "可用内存不足，请关闭其他程序或使用较小的图片。"))
            except ImageToolError as error:
                self._events.put(("error", str(error)))
            except Exception as error:
                self._events.put(("error", f"处理未完成：{error}"))
            else:
                self._events.put(("complete", result))

        threading.Thread(target=worker, daemon=True, name="image-worker").start()

    def _drain_events(self) -> None:
        """由 Tk 主线程消费工作线程消息，避免跨线程访问控件。"""
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "progress":
                    fraction, text = value
                    self.progress.set(max(0, min(100, fraction * 100)))
                    if self._batch_running and self._cancel_batch.is_set():
                        text = "正在停止 · " + text
                    self.status.set(text)
                    continue
                self._busy = False
                self._batch_running = False
                callback = self._on_success
                self._on_success = None
                self._set_controls()
                if kind == "complete":
                    self.progress.set(100)
                    if callback is not None:
                        callback(value)
                else:
                    self.progress.set(0)
                    self.status.set("处理未完成 · 请检查输入和口令")
                    messagebox.showerror("操作未完成", value, parent=self.root)
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_id = self.root.after(70, self._drain_events)

    def _close(self) -> None:
        """退出前避免中断正常保存，并提醒尚未保存的结果。"""
        if self._busy:
            messagebox.showinfo("正在处理", "请等待当前操作完成后再关闭。", parent=self.root)
            return
        if self.result_image is not None and not messagebox.askokcancel(
            "关闭工具", "关闭后内存中的处理结果会丢失，请确认已保存需要的图片。", parent=self.root,
        ):
            return
        self._closed = True
        self.root.after_cancel(self._poll_id)
        self.root.destroy()


def run(initial_path: str | None = None) -> None:
    """设置 Windows DPI 感知并启动桌面应用，可选载入一张图片。"""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    app = ImageToolApp(root)
    if initial_path:
        root.after(100, lambda: app.load_path(Path(initial_path)))
    root.mainloop()
