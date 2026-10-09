"""
gui.py - 纯图片 PDF -> EPUB 转换工具的图形界面 (tkinter)

支持批量添加 PDF、拖拽导入、逐个转换、实时日志与进度。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from converter import (DEVICES, EEGO_A4_HEIGHT, EEGO_A4_WIDTH, ConvertOptions,
                       convert, device_options, safe_filename)

APP_TITLE = "图片 PDF 转 EPUB"
APP_VER = "1.1"

# ---------------------------------------------------------------- 拖放支持
# tkinterdnd2 提供原生拖放（内部是 TkDND）。
# 万一加载失败（例如缺 tkdnd 动态库），程序照常运行，只是拖放不可用。
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _DND_TK = TkinterDnD.Tk   # 能启用拖放的根窗口类
    DND_AVAILABLE = True
except Exception:  # noqa: BLE001
    DND_FILES = None
    TkinterDnD = None
    _DND_TK = None
    DND_AVAILABLE = False

DRAG_HINT_ON = "把 PDF 文件或文件夹拖到这里 ⬇"
DRAG_HINT_OFF = "（可批量，依次转换）"


def parse_drop_paths(data: str) -> list[str]:
    """
    解析 tkdnd 传来的文件列表字符串。

    Tk 的列表格式里，含空格/中文的路径会用 {} 包起来，例如：
        {C:/我的 文档/a.pdf} C:/b.pdf {C:/c d/e.pdf}
    这里按 Tcl 规则正确切分。
    """
    paths: list[str] = []
    if not data:
        return paths

    buf: list[str] = []
    in_brace = False

    def flush() -> None:
        if buf:
            s = "".join(buf).strip()
            if s:
                paths.append(s)
            buf.clear()

    for ch in data:
        if ch == "{":
            # 遇到 { 说明前一个裸路径结束了
            flush()
            in_brace = True
        elif ch == "}" and in_brace:
            flush()
            in_brace = False
        elif in_brace:
            buf.append(ch)
        elif ch in " \t\r\n":
            flush()
        else:
            buf.append(ch)
    flush()

    return paths


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=10)
        self.master = master
        self.files: list[str] = []
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.msg_q: queue.Queue = queue.Queue()
        self._drop_widgets: list[tk.Widget] = []

        self._build_ui()
        self._setup_dnd()
        self._on_preset_change()   # 初始化预设相关控件的启用状态
        self.after(100, self._pump)

    # ---------------- 拖放 ----------------

    def _setup_dnd(self) -> None:
        """给列表和整个窗口注册拖放目标。"""
        if not DND_AVAILABLE:
            self._log("提示：当前环境拖放不可用，请用「添加 PDF 文件…」按钮。")
            return

        targets = [self.tree, self, self.master, self.drop_hint]
        self._drop_widgets = [w for w in targets if w is not None]

        bound = 0
        for w in self._drop_widgets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
                w.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                w.dnd_bind("<<DragLeave>>", self._on_drag_leave)
                bound += 1
            except Exception:  # noqa: BLE001 - 个别控件不支持时跳过
                continue

        if bound:
            self.drop_hint.configure(
                text=DRAG_HINT_ON + "（也可以直接拖到窗口任意位置）", foreground="#0a7d28")
        else:
            self.drop_hint.configure(text=DRAG_HINT_OFF, foreground="#777")

    def _on_drag_enter(self, _event=None) -> None:
        try:
            self.drop_hint.configure(text="松手即可添加 ⬇", foreground="#0a7d28")
        except tk.TclError:
            pass

    def _on_drag_leave(self, _event=None) -> None:
        if DND_AVAILABLE:
            try:
                self.drop_hint.configure(
                    text=DRAG_HINT_ON + "（也可以直接拖到窗口任意位置）",
                    foreground="#0a7d28")
            except tk.TclError:
                pass

    def _on_drop(self, event) -> None:
        """处理拖入的文件/文件夹。"""
        raw = getattr(event, "data", "") or ""
        paths = parse_drop_paths(raw)
        if not paths:
            return

        pdfs: list[str] = []
        dirs: list[str] = []
        skipped: list[str] = []

        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                dirs.append(p)
            elif os.path.isfile(p) and p.lower().endswith(".pdf"):
                pdfs.append(p)
            else:
                skipped.append(os.path.basename(p))

        # 文件夹：只扫一层，避免误拖整个磁盘时卡死
        for d in dirs:
            try:
                for f in sorted(os.listdir(d)):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp) and f.lower().endswith(".pdf"):
                        pdfs.append(fp)
            except OSError:
                pass

        if pdfs:
            self._add(pdfs)
        if skipped and not pdfs:
            self._log(f"已忽略 {len(skipped)} 个非 PDF 文件：{', '.join(skipped[:5])}")
        elif skipped:
            self._log(f"（已忽略 {len(skipped)} 个非 PDF 文件）")
        if dirs and not pdfs:
            self._log("拖入的文件夹里没有 PDF 文件。")

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        self.grid(row=0, column=0, sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # --- 顶部：按钮 ---
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(top, text="添加 PDF 文件…", command=self.add_files).pack(side="left")
        ttk.Button(top, text="添加文件夹…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="移除选中", command=self.remove_selected).pack(side="left")
        ttk.Button(top, text="清空列表", command=self.clear_files).pack(side="left", padx=4)
        ttk.Label(top, text="（可批量，依次转换）", foreground="#777").pack(side="left", padx=8)

        # --- 拖放提示条 ---
        self.drop_hint = ttk.Label(
            self, text=DRAG_HINT_ON if DND_AVAILABLE else DRAG_HINT_OFF,
            foreground="#0a7d28" if DND_AVAILABLE else "#777",
            anchor="center", padding=6)
        self.drop_hint.grid(row=8, column=0, sticky="ew", pady=(8, 0))

        # --- 中部：文件列表 ---
        mid = ttk.LabelFrame(self, text="待转换文件", padding=6)
        mid.grid(row=1, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(mid, columns=("name", "size", "status"),
                                 show="headings", height=8, selectmode="extended")
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("status", text="状态")
        self.tree.column("name", width=380, anchor="w")
        self.tree.column("size", width=90, anchor="e", stretch=False)
        self.tree.column("status", width=150, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        # --- 设备预设 ---
        pre = ttk.LabelFrame(self, text="目标设备", padding=6)
        pre.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self.var_preset = tk.StringVar(value="通用（不缩放）")
        self.var_screen = tk.StringVar(value=f"{EEGO_A4_WIDTH}x{EEGO_A4_HEIGHT}")
        self.var_headroom = tk.StringVar(value="1.00x")
        self.var_resample = tk.StringVar(value="双线性")
        self.var_eego_fmt = tk.StringVar(value="原始格式")
        self.var_eego_q = tk.IntVar(value=90)

        # 设备下拉项：显示名 + 屏幕（界面上按设备的习惯标注）
        self._device_values = ["通用（不缩放）"] + [
            f"{name} ({w}x{h})" for name, w, h in DEVICES]

        ttk.Label(pre, text="设备:").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(pre, textvariable=self.var_preset, width=24, state="readonly",
                          values=self._device_values)
        cb.grid(row=0, column=1, sticky="w", padx=(0, 16))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_change())

        self.lbl_scr = ttk.Label(pre, text="屏幕:")
        self.lbl_scr.grid(row=0, column=2, sticky="e")
        self.ent_scr = ttk.Entry(pre, textvariable=self.var_screen, width=10)
        self.ent_scr.grid(row=0, column=3, sticky="w", padx=(0, 16))

        self.lbl_hr = ttk.Label(pre, text="宽度倍数:")
        self.lbl_hr.grid(row=0, column=4, sticky="e")
        self.cb_hr = ttk.Combobox(pre, textvariable=self.var_headroom, width=6,
                                  state="readonly",
                                  values=["1.00x", "1.15x", "1.25x", "1.50x", "2.00x"])
        self.cb_hr.grid(row=0, column=5, sticky="w", padx=(0, 16))

        self.lbl_rs = ttk.Label(pre, text="算法:")
        self.lbl_rs.grid(row=0, column=6, sticky="e")
        self.cb_rs = ttk.Combobox(pre, textvariable=self.var_resample, width=8,
                                  state="readonly", values=["双线性", "Lanczos"])
        self.cb_rs.grid(row=0, column=7, sticky="w")

        # --- 第二行：EEGO 专用输出格式 ---
        self.lbl_efmt = ttk.Label(pre, text="输出格式:")
        self.lbl_efmt.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.cb_efmt = ttk.Combobox(pre, textvariable=self.var_eego_fmt, width=12,
                                    state="readonly",
                                    values=["原始格式", "统一 JPG", "统一 PNG"])
        self.cb_efmt.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.cb_efmt.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_change())

        # 选了 JPEG 才用得到质量
        self.lbl_eq = ttk.Label(pre, text="JPG 质量:")
        self.lbl_eq.grid(row=1, column=2, sticky="e", pady=(6, 0))
        qf2 = ttk.Frame(pre)
        qf2.grid(row=1, column=3, sticky="w", pady=(6, 0))
        self.scl_eq = ttk.Scale(qf2, from_=40, to=100, variable=self.var_eego_q,
                                orient="horizontal", length=90)
        self.scl_eq.pack(side="left")
        ttk.Label(qf2, textvariable=self.var_eego_q, width=3).pack(side="left")

        self.lbl_pre_hint = ttk.Label(
            pre, text="", foreground="#0a5", wraplength=840, justify="left")
        self.lbl_pre_hint.grid(row=2, column=0, columnspan=8, sticky="w", pady=(6, 0))

        # --- 选项 ---
        opt = ttk.LabelFrame(self, text="转换选项（选「通用」时生效）", padding=6)
        opt.grid(row=3, column=0, sticky="ew", pady=8)
        self.opt_frame = opt

        self.var_fmt = tk.StringVar(value="原始格式")
        self.var_scale = tk.StringVar(value="保持原分辨率")
        self.var_quality = tk.IntVar(value=90)
        self.var_gray = tk.BooleanVar(value=False)
        self.var_trim = tk.BooleanVar(value=False)
        self.var_display = tk.StringVar(value="自适应页宽")
        self.var_cover = tk.BooleanVar(value=True)

        r = 0
        ttk.Label(opt, text="图片格式:").grid(row=r, column=0, sticky="w")
        ttk.Combobox(opt, textvariable=self.var_fmt, width=14, state="readonly",
                     values=["原始格式", "统一 JPG", "统一 PNG"]).grid(
            row=r, column=1, sticky="w", padx=(0, 16))

        ttk.Label(opt, text="缩放:").grid(row=r, column=2, sticky="w")
        ttk.Combobox(opt, textvariable=self.var_scale, width=16, state="readonly",
                     values=["保持原分辨率", "限制宽≤1600", "限制宽≤1200",
                             "限制宽≤1000", "限制宽≤800"]).grid(
            row=r, column=3, sticky="w", padx=(0, 16))

        ttk.Label(opt, text="显示:").grid(row=r, column=4, sticky="w")
        ttk.Combobox(opt, textvariable=self.var_display, width=14, state="readonly",
                     values=["自适应页宽", "原始像素尺寸"]).grid(row=r, column=5, sticky="w")

        r += 1
        ttk.Checkbutton(opt, text="灰度化（缩小体积）", variable=self.var_gray).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opt, text="自动裁白边", variable=self.var_trim).grid(
            row=r, column=2, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opt, text="首张图片设为封面", variable=self.var_cover).grid(
            row=r, column=4, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(opt, text="JPG 质量:").grid(row=r, column=6, sticky="e", pady=(6, 0))
        qf = ttk.Frame(opt)
        qf.grid(row=r, column=7, sticky="w", pady=(6, 0))
        ttk.Scale(qf, from_=40, to=100, variable=self.var_quality,
                  orient="horizontal", length=90).pack(side="left")
        ttk.Label(qf, textvariable=self.var_quality, width=3).pack(side="left")

        # --- 输出目录 ---
        out = ttk.Frame(self)
        out.grid(row=4, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        self.var_outdir = tk.StringVar(value="")
        ttk.Label(out, text="输出目录:").grid(row=0, column=0, sticky="w")
        ttk.Entry(out, textvariable=self.var_outdir).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(out, text="选择…", command=self.choose_outdir).grid(row=0, column=2)
        ttk.Label(out, text="留空 = 与源文件同目录", foreground="#777").grid(
            row=1, column=1, sticky="w", padx=6)

        # --- 操作按钮 ---
        act = ttk.Frame(self)
        act.grid(row=5, column=0, sticky="ew", pady=8)
        self.btn_start = ttk.Button(act, text="开始转换", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(act, text="取消", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.btn_open = ttk.Button(act, text="打开输出目录", command=self.open_outdir)
        self.btn_open.pack(side="left", padx=6)

        # --- 进度与日志 ---
        self.pb = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.pb.grid(row=6, column=0, sticky="ew")
        self.lbl = ttk.Label(self, text="就绪", foreground="#333")
        self.lbl.grid(row=7, column=0, sticky="w", pady=(4, 0))

        logf = ttk.LabelFrame(self, text="日志", padding=4)
        logf.grid(row=9, column=0, sticky="nsew", pady=(6, 0))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.rowconfigure(9, weight=1)
        self.log = tk.Text(logf, height=7, wrap="word", state="disabled",
                           background="#1e1e1e", foreground="#d4d4d4",
                           insertbackground="#d4d4d4", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=lsb.set)

    # ---------------- 设备预设 ----------------

    def _is_eego(self) -> bool:
        """当前是否选了某个设备预设（非「通用」）。"""
        return not self.var_preset.get().startswith("通用")

    def _selected_device(self) -> tuple[str, int, int] | None:
        """从下拉文本里解析出 (名称, 宽, 高)。"""
        txt = self.var_preset.get()
        for name, w, h in DEVICES:
            if txt.startswith(name):
                return name, w, h
        return None

    def _screen_size(self) -> tuple[int, int]:
        """解析「宽x高」；失败时退回当前设备（或默认）的屏幕尺寸。"""
        try:
            w, h = self.var_screen.get().lower().replace("×", "x").split("x")
            return max(1, int(w.strip())), max(1, int(h.strip()))
        except Exception:
            dev = self._selected_device()
            if dev:
                return dev[1], dev[2]
            return EEGO_A4_WIDTH, EEGO_A4_HEIGHT

    def _headroom(self) -> float:
        try:
            return float(self.var_headroom.get().rstrip("xX"))
        except ValueError:
            return 1.0

    def _resample_name(self) -> str:
        return "lanczos" if self.var_resample.get().lower().startswith("lan") else "bilinear"

    def _eego_format(self) -> str:
        return {"原始格式": "auto", "统一 JPG": "jpg", "统一 PNG": "png"}[
            self.var_eego_fmt.get()]

    def _on_preset_change(self) -> None:
        """切换预设时同步屏幕尺寸、启用/停用相关控件并给出说明。"""
        e = self._is_eego()

        # 选了具体设备就自动填上它的屏幕尺寸
        dev = self._selected_device()
        if dev:
            self.var_screen.set(f"{dev[1]}x{dev[2]}")

        for w in (self.lbl_scr, self.ent_scr, self.lbl_hr, self.cb_hr,
                  self.lbl_rs, self.cb_rs, self.lbl_efmt, self.cb_efmt):
            try:
                w.configure(state=("readonly" if isinstance(w, ttk.Combobox)
                                   and e else ("normal" if e else "disabled")))
            except tk.TclError:
                pass

        # JPG 质量：只有设备模式且选了 JPG 才可用
        jpg_on = e and self._eego_format() == "jpg"
        for w in (self.lbl_eq, self.scl_eq):
            try:
                w.configure(state="normal" if jpg_on else "disabled")
            except tk.TclError:
                pass

        # 「通用」模式下才用得到那组手动选项
        for child in self.opt_frame.winfo_children():
            try:
                if isinstance(child, (ttk.Combobox, ttk.Checkbutton, ttk.Scale, ttk.Entry)):
                    child.configure(state=("disabled" if e else
                                           ("readonly" if isinstance(child, ttk.Combobox)
                                            else "normal")))
            except tk.TclError:
                pass

        if e:
            w, h = self._screen_size()
            tw = int(round(w * self._headroom()))
            f = self.var_eego_fmt.get()
            fmt_note = ("保持原格式" if f == "原始格式" else f"统一转为 {f[-3:]}")
            name = dev[0] if dev else "设备"
            self.lbl_pre_hint.configure(
                text=f"{name} ({w}x{h})：把图片按宽度缩放到 {tw}px（屏宽 {w} × 倍数 "
                     f"{self._headroom():.2f}），{fmt_note}；"
                     f"不转灰度、不裁边、不放大。首张图片自动作封面。")
        else:
            self.lbl_pre_hint.configure(text="")

    # ---------------- 文件列表 ----------------

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择图片型 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        self._add(paths)

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if not d:
            return
        found = []
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".pdf"):
                    found.append(os.path.join(root, f))
        if not found:
            messagebox.showinfo(APP_TITLE, "该文件夹下没有找到 PDF 文件。")
            return
        self._add(found)

    def _add(self, paths) -> None:
        added = 0
        for p in paths or []:
            p = os.path.abspath(p)
            if p in self.files:
                continue
            self.files.append(p)
            try:
                size = os.path.getsize(p)
                size_s = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"
            except OSError:
                size_s = "?"
            self.tree.insert("", "end", iid=p, values=(os.path.basename(p), size_s, "等待"))
            added += 1
        self._log(f"添加 {added} 个文件，共 {len(self.files)} 个。")

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            if iid in self.files:
                self.files.remove(iid)
            self.tree.delete(iid)

    def clear_files(self) -> None:
        self.files.clear()
        self.tree.delete(*self.tree.get_children())

    def _set_status(self, path: str, status: str) -> None:
        if self.tree.exists(path):
            v = list(self.tree.item(path, "values"))
            v[2] = status
            self.tree.item(path, values=v)

    # ---------------- 选项 ----------------

    def _scale_value(self) -> int | None:
        m = {"保持原分辨率": None, "限制宽≤1600": 1600, "限制宽≤1200": 1200,
             "限制宽≤1000": 1000, "限制宽≤800": 800}
        return m.get(self.var_scale.get())

    def _build_options(self, pdf_path: str) -> ConvertOptions:
        title = os.path.splitext(os.path.basename(pdf_path))[0]

        # 设备预设：按屏幕宽度缩放；格式可选，其余不动
        if self._is_eego():
            w, h = self._screen_size()
            opts = device_options(title=title, width=w, height=h,
                                  headroom=self._headroom(),
                                  resample=self._resample_name())
            opts.use_cover = bool(self.var_cover.get())
            # EEGO 区里的格式选择
            eft = self._eego_format()
            opts.image_format = eft
            if eft == "jpg":
                opts.jpeg_quality = int(self.var_eego_q.get())
            return opts

        fmt = {"原始格式": "auto", "统一 JPG": "jpg", "统一 PNG": "png"}[self.var_fmt.get()]
        return ConvertOptions(
            max_dimension=self._scale_value(),
            jpeg_quality=int(self.var_quality.get()),
            image_format=fmt,
            grayscale=bool(self.var_gray.get()),
            trim_borders=bool(self.var_trim.get()),
            title=title,
            display_mode="fit" if self.var_display.get() == "自适应页宽" else "native",
            use_cover=bool(self.var_cover.get()),
            resample=self._resample_name(),
        )

    def choose_outdir(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_outdir.set(d)

    def open_outdir(self) -> None:
        d = self.var_outdir.get().strip()
        if not d:
            if self.files:
                d = os.path.dirname(self.files[0])
            else:
                messagebox.showinfo(APP_TITLE, "还没有输出目录。")
                return
        if not os.path.isdir(d):
            messagebox.showwarning(APP_TITLE, "目录不存在。")
            return
        try:
            os.startfile(d)  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", d])

    # ---------------- 执行 ----------------

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning(APP_TITLE, "请先添加至少一个 PDF 文件。")
            return

        self.cancel_flag.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.pb.configure(value=0)
        self._log("=" * 46)
        self._log(f"开始转换，共 {len(self.files)} 个文件。")
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self._log("已请求取消，正在停止…")
            self.btn_cancel.configure(state="disabled")

    def _run(self) -> None:
        outdir = self.var_outdir.get().strip()
        results = []
        for n, pdf in enumerate(self.files, 1):
            if self.cancel_flag.is_set():
                self.msg_q.put(("status", (pdf, "已取消")))
                break

            self.msg_q.put(("file_start", (pdf, n, len(self.files))))
            self.msg_q.put(("status", (pdf, "转换中…")))

            if outdir:
                out_path = os.path.join(
                    outdir, safe_filename(os.path.splitext(os.path.basename(pdf))[0]) + ".epub")
            else:
                out_path = None

            def on_prog(cur: int, tot: int, msg: str, _n=n) -> None:
                self.msg_q.put(("progress", (cur, tot, f"[{_n}/{len(self.files)}] {msg}")))

            opts = self._build_options(pdf)
            res = convert(pdf, out_path=out_path, opts=opts,
                          progress=on_prog, should_stop=self.cancel_flag.is_set)

            if res.ok:
                self.msg_q.put(("status", (pdf, "完成 ✓")))
                src_mb = res.src_size / 1048576
                out_mb = res.out_size / 1048576
                ratio = (res.out_size / res.src_size * 100) if res.src_size else 0
                line = (f"  完成: {os.path.basename(res.out_path)}  "
                        f"{res.pages} 页  {src_mb:.1f} -> {out_mb:.1f} MB "
                        f"(原体积 {ratio:.0f}%)")
                if opts.use_cover:
                    line += "  [含封面]"
                self.msg_q.put(("log", line))
                results.append(True)
            else:
                self.msg_q.put(("status", (pdf, "失败 ✗")))
                self.msg_q.put(("log", f"  失败: {res.error}"))
                results.append(False)

        self.msg_q.put(("done", results))

    # ---------------- 主线程消息泵 ----------------

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()

                if kind == "log":
                    self._log(payload)
                elif kind == "status":
                    path, st = payload
                    self._set_status(path, st)
                elif kind == "file_start":
                    pdf, n, total = payload
                    self._log(f"[{n}/{total}] {os.path.basename(pdf)}")
                elif kind == "progress":
                    cur, tot, msg = payload
                    pct = int(cur * 100 / tot) if tot else 0
                    self.pb.configure(value=pct)
                    self.lbl.configure(text=msg)
                elif kind == "done":
                    ok = sum(1 for r in payload if r)
                    bad = len(payload) - ok
                    self.pb.configure(value=100 if bad == 0 else self.pb["value"])
                    self.lbl.configure(text=f"结束：成功 {ok}，失败 {bad}")
                    self._log(f"全部结束：成功 {ok}，失败 {bad}。")
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    # 用 TkinterDnD 的根窗口才能启用原生拖放；不可用时退回普通 Tk
    if DND_AVAILABLE:
        try:
            root = _DND_TK()
        except Exception:  # noqa: BLE001
            root = tk.Tk()
    else:
        root = tk.Tk()

    root.title(f"{APP_TITLE} v{APP_VER}")
    root.geometry("900x780")
    root.minsize(780, 660)
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
