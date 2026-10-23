"""桌面工作台视觉规范：墨蓝顶栏、青绿重点色和轻量原生控件样式。"""

from tkinter import PhotoImage, ttk

BACKGROUND = "#F2F5F7"
SURFACE = "#FFFFFF"
INK = "#20323F"
MUTED = "#72818E"
ACCENT = "#138579"
ACCENT_SOFT = "#E8F5F1"
BORDER = "#E1E8EC"
CANVAS = "#F7F9FA"
NAVY = "#172D38"
FONT = "Microsoft YaHei UI"


def configure_styles(root) -> None:
    """统一按钮、分段选择、输入框、表格和滚动条，保持原生键盘操作。"""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=BACKGROUND)
    style.configure(
        "TButton", font=(FONT, 10), padding=(14, 10), relief="raised",
        background=SURFACE, foreground=INK, borderwidth=1,
        bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE,
        focuscolor=ACCENT, focusthickness=1,
    )
    style.map(
        "TButton", background=[("disabled", "#F4F6F7"), ("active", "#EDF3F4")],
        foreground=[("disabled", "#9AA6AE")], bordercolor=[("focus", ACCENT), ("active", "#C7D7DA")],
    )
    style.configure(
        "Primary.TButton", font=(FONT, 10, "bold"), padding=(20, 12),
        background=ACCENT, foreground="white", bordercolor=ACCENT, relief="flat",
        lightcolor=ACCENT, darkcolor=ACCENT, focuscolor="white",
    )
    style.map(
        "Primary.TButton", background=[("disabled", "#DCE6E6"), ("active", "#0C7066")],
        foreground=[("disabled", "#7F9996")], bordercolor=[("disabled", "#DCE6E6"), ("active", "#0C7066")],
        lightcolor=[("disabled", "#DCE6E6"), ("active", "#0C7066")],
        darkcolor=[("disabled", "#DCE6E6"), ("active", "#0C7066")],
    )
    style.configure("Quiet.TButton", borderwidth=0, background=BACKGROUND, foreground=MUTED)
    style.configure("Stop.TButton", foreground="#A25349")
    style.layout("Segment.TRadiobutton", [
        ("Radiobutton.padding", dict(sticky="nswe", children=[
            ("Radiobutton.focus", dict(sticky="nswe", children=[
                ("Radiobutton.label", dict(sticky="nswe")),
            ])),
        ])),
    ])
    style.configure(
        "Segment.TRadiobutton", background="#F0F4F5", foreground=MUTED,
        padding=(16, 10), font=(FONT, 10, "bold"), anchor="center", focuscolor=ACCENT,
    )
    style.map(
        "Segment.TRadiobutton", background=[("selected", ACCENT_SOFT), ("active", "#E9F0F1")],
        foreground=[("disabled", "#9AA6AE"), ("selected", ACCENT), ("active", INK)],
    )
    style.configure(
        "TCheckbutton", background=SURFACE, foreground=MUTED, font=(FONT, 9),
        padding=(0, 3), focuscolor=ACCENT, indicatorbackground=SURFACE,
    )
    style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("disabled", "#A9B2B8")])
    for name in ("TEntry", "TCombobox"):
        style.configure(
            name, padding=8, fieldbackground=SURFACE, foreground=INK,
            bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE,
            arrowcolor=MUTED, arrowsize=13, relief="flat", background=SURFACE,
        )
        style.map(
            name, bordercolor=[("focus", ACCENT)],
            fieldbackground=[("disabled", "#F2F5F6"), ("readonly", SURFACE)],
            foreground=[("disabled", "#8D9BA4")],
        )
    style.configure("Path.TEntry", padding=5, borderwidth=0)
    style.configure(
        "Files.Treeview", font=(FONT, 10), rowheight=32, background=SURFACE,
        fieldbackground=SURFACE, foreground=INK, borderwidth=0,
    )
    style.configure(
        "Files.Treeview.Heading", font=(FONT, 9, "bold"), padding=(10, 11),
        background="#F0F4F6", foreground=MUTED, relief="flat", borderwidth=0,
    )
    style.map("Files.Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", "#136C63")])
    style.map("Files.Treeview.Heading", background=[("active", "#E7EEF0")])
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            orientation + ".TScrollbar", background="#D7E0E5", troughcolor="#F7F9FA",
            borderwidth=0, arrowsize=11, arrowcolor="#83939F", relief="flat",
            bordercolor="#F7F9FA", lightcolor="#D7E0E5", darkcolor="#D7E0E5",
        )
    style.configure(
        "Horizontal.TProgressbar", background=ACCENT, troughcolor="#E4EBED",
        borderwidth=0, troughborderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4,
    )
    # 修复 clam 在不同尺寸下进度条边框挤掉填充的问题：用原生 Tk 色块定义细线元素。
    root._progress_images = []
    for element, color in (("trough", "#E4EBED"), ("pbar", ACCENT)):
        picture = PhotoImage(master=root, width=2, height=4)
        picture.put(color, to=(0, 0, 2, 4))
        root._progress_images.append(picture)
        style.element_create("Work.Progressbar." + element, "image", picture, sticky="nswe")
    style.layout("Horizontal.TProgressbar", [
        ("Work.Progressbar.trough", dict(sticky="nswe", children=[
            ("Work.Progressbar.pbar", dict(side="left", sticky="ns")),
        ])),
    ])
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
