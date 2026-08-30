import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# 路径基准：打包成 exe（PyInstaller）时 __file__ 指向临时解压目录，
# 需改用 sys.executable 所在目录，保证配置/bat 与 exe 同目录
if getattr(sys, "frozen", False):  # 打包后
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:  # 直接运行脚本
    SCRIPT_DIR = Path(__file__).resolve().parent
BAT_PATH = SCRIPT_DIR / "NVFP4.bat"
CONFIG_PATH = SCRIPT_DIR / "local_llm_gui_config.json"

# ============================================================
# 主题配色（深色现代风格）
# ============================================================
COLORS = {
    "bg": "#ffffff",
    "panel": "#f5f5f7",
    "panel_light": "#e8e8ec",
    "fg": "#1d1d1f",
    "fg_dim": "#86868b",
    "accent": "#0071e3",
    "green": "#34c759",
    "red": "#ff3b30",
    "yellow": "#ff9500",
    "entry_bg": "#ffffff",
}

# ============================================================
# 参数分组：(key, 中文标签, 说明)
# ============================================================
GROUP_BASE = [
    ("HOST", "Host", "Server listen address"),
    ("CHECK_HOST", "Check Host", "IP for health check"),
    ("PORT", "Port", "Service port"),
    ("ALIAS", "Alias", "OpenAI-compatible alias"),
]
GROUP_MODEL = [
    ("MODEL_PATH", "Model", "GGUF model path (required)"),
    ("MMPROJ_PATH", "Vision Proj", "mmproj file path (optional)"),
    ("CHAT_TEMPLATE", "Chat Template", "Jinja template path (optional)"),
    ("CTX_SIZE", "Context Size", "Context size"),
    ("TEMPERATURE", "Temperature", "Sampling temperature"),
]
GROUP_ADVANCED = [
    ("LLAMA_DIR", "llama Dir", "Directory of llama-server.exe (required)"),
    ("THREADS", "Threads", "-t thread count"),
    ("TBATCH", "Thread Batch", "-tb thread batch size"),
    ("BATCH", "Batch", "-b batch size"),
    ("UBATCH", "UBatch", "-ub unit batch size"),
    ("TENSOR_SPLIT", "Tensor Split", "Multi-GPU split ratio"),
    ("SPEC_TYPE", "Spec Type", "--spec-type, e.g. draft-mtp (optional)"),
    ("SPEC_DRAFT_N_MAX", "Spec Draft N-Max", "--spec-draft-n-max (optional)"),
    ("SPEC_DRAFT_P_MIN", "Spec Draft P-Min", "--spec-draft-p-min (optional)"),
    ("START_TIMEOUT_S", "Start Timeout (s)", "Timeout waiting for ready"),
    ("API_KEY", "API Key", "API access key"),
]

DEFAULT_VALUES = {
    # 核心：用户只需填写 llama-server 所在目录 + 模型路径
    "LLAMA_DIR": "",
    "MODEL_PATH": "",
    # 可选：视觉投影 / 聊天模板（留空则不启用）
    "MMPROJ_PATH": "",
    "CHAT_TEMPLATE": "",
    "HOST": "0.0.0.0",
    "CHECK_HOST": "127.0.0.1",
    "PORT": "8080",
    "CTX_SIZE": "8192",
    "ALIAS": "",
    "THREADS": "8",
    "TBATCH": "2048",
    "BATCH": "2048",
    "UBATCH": "2048",
    "TENSOR_SPLIT": "",
    "SPEC_TYPE": "",
    "SPEC_DRAFT_N_MAX": "2",
    "SPEC_DRAFT_P_MIN": "0.6",
    "START_TIMEOUT_S": "180",
    "API_KEY": "",
    "TEMPERATURE": "0.8",
}


class LLMManagerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Local LLM Manager")
        self.root.geometry("1320x820")
        self.root.minsize(1080, 700)

        self.process = None
        self.server_ready = False
        self.closing = False
        self.config = self.load_defaults()
        self.entries = {}
        self.status_var = tk.StringVar(value="● Stopped")
        # token counter
        self.total_tokens = 0

        self.setup_style()
        self.build_ui()
        self.load_saved_config()
        self.append_log("GUI started. Set your llama-server directory and model path, then press Start.")
        self.root.after(500, self.refresh_status)
        self.root.after(800, self.poll_gpu)

    # ------------------------------------------------------------
    # 字体（优先 Apple 字体，缺失时回退到系统字体）
    # ------------------------------------------------------------
    def detect_fonts(self):
        import tkinter.font as tkfont
        try:
            families = set(tkfont.families(self.root))
        except Exception:
            families = set()

        # UI 字体：优先 Apple 的 SF Pro 系列
        for name in ("SF Pro Display", "SF Pro Text", "SF Pro",
                     "Apple System Font", "AppleSystemUIFont", "Helvetica Neue",
                     "Segoe UI"):
            if name in families:
                self.ui_font = name
                break
        else:
            self.ui_font = "Segoe UI"

        # 等宽字体：优先 Apple 的 SF Mono / Menlo
        for name in ("SF Mono", "Menlo", "Apple Symbols Mono", "Consolas", "Courier New"):
            if name in families:
                self.mono_font = name
                break
        else:
            self.mono_font = "Consolas"

    # ------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------
    def setup_style(self):
        self.detect_fonts()
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        c = COLORS
        style.configure(".", background=c["bg"], foreground=c["fg"], font=(self.ui_font, 10))
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["panel"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("Card.TLabel", background=c["panel"], foreground=c["fg"])
        style.configure("Title.TLabel", background=c["bg"], foreground=c["accent"],
                        font=(self.ui_font, 18, "bold"))
        style.configure("Dim.TLabel", background=c["bg"], foreground=c["fg_dim"])
        style.configure("StatusIdle.TLabel", background=c["bg"], foreground=c["fg_dim"],
                        font=(self.ui_font, 11, "bold"))
        style.configure("StatusRun.TLabel", background=c["bg"], foreground=c["green"],
                        font=(self.ui_font, 11, "bold"))
        style.configure("StatusStart.TLabel", background=c["bg"], foreground=c["yellow"],
                        font=(self.ui_font, 11, "bold"))
        style.configure("TEntry", background=c["entry_bg"], foreground=c["fg"],
                        fieldbackground=c["entry_bg"], insertcolor=c["fg"], padding=5)
        style.map("TEntry", fieldbackground=[("focus", c["entry_bg"])])

        style.configure("Start.TButton", background=c["green"], foreground="#ffffff",
                        font=(self.ui_font, 11, "bold"), padding=8)
        style.map("Start.TButton", background=[("active", "#5fd87d")])
        style.configure("Stop.TButton", background=c["red"], foreground="#ffffff",
                        font=(self.ui_font, 11, "bold"), padding=8)
        style.map("Stop.TButton", background=[("active", "#ff6b60")])
        style.configure("TButton", background=c["panel_light"], foreground=c["fg"],
                        font=(self.ui_font, 10, "bold"), padding=8)
        style.map("TButton", background=[("active", c["accent"])],
                  foreground=[("active", "#ffffff")])

        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["panel"], foreground=c["fg_dim"],
                        font=(self.ui_font, 10, "bold"), padding=(16, 8))
        style.map("TNotebook.Tab",
                  background=[("selected", c["accent"])],
                  foreground=[("selected", "#ffffff")])

    # ------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------
    def _clean_value(self, value):
        v = str(value).strip()
        # 处理 set "KEY=VALUE" 整体加引号：value 可能带末尾孤立引号
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        elif v.endswith('"') and len(v) >= 1:
            v = v[:-1]
        return v.strip()

    def _collect_bat_vars(self):
        raw = {}
        if BAT_PATH.exists():
            try:
                with BAT_PATH.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line.lower().startswith("set"):
                            continue
                        rest = line[3:].lstrip()
                        m = re.match(r'^"?([A-Za-z0-9_]+)=(.*)"?$', rest)
                        if not m:
                            continue
                        key = m.group(1)
                        value = self._clean_value(m.group(2))
                        raw[key] = value
            except Exception:
                pass
        # bat 中 SCRIPT_DIR=%~dp0，等价于本脚本所在目录
        raw["SCRIPT_DIR"] = str(SCRIPT_DIR) + os.sep
        return raw

    def _expand(self, value, raw):
        for _ in range(6):
            def repl(mm):
                return raw.get(mm.group(1), mm.group(0))
            new = re.sub(r'%([A-Za-z0-9_]+)%', repl, value)
            if new == value:
                break
            value = new
        return value

    def load_defaults(self):
        data = DEFAULT_VALUES.copy()
        raw = self._collect_bat_vars()
        for key in data:
            if key in raw:
                data[key] = self._expand(self._clean_value(raw[key]), raw)
        return data

    def load_saved_config(self):
        if not CONFIG_PATH.exists():
            return
        raw = self._collect_bat_vars()
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            for key, value in saved.items():
                if key in self.entries:
                    # 修复历史脏数据：剥离引号 + 展开 %VAR%
                    clean = self._expand(self._clean_value(value), raw)
                    self.entries[key].delete(0, tk.END)
                    self.entries[key].insert(0, clean)
            self.append_log(f"Loaded saved config: {CONFIG_PATH.name}")
        except Exception:
            self.append_log("Failed to load saved config. Falling back to defaults.")

    def save_config(self):
        current = {}
        for key in DEFAULT_VALUES:
            if key not in self.entries:
                continue
            try:
                current[key] = self.entries[key].get().strip()
            except tk.TclError:
                # 控件已销毁（窗口关闭中），跳过
                continue
        if not current:
            return
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            self.append_log(f"Config saved: {CONFIG_PATH.name}")
        except Exception as exc:
            self.append_log(f"Failed to save config: {exc}")

    def reset_defaults(self):
        for key, value in self.load_defaults().items():
            if key in self.entries:
                self.entries[key].delete(0, tk.END)
                self.entries[key].insert(0, str(value))
        self.append_log("Restored default parameters.")

    # ------------------------------------------------------------
    # 文件 / 目录选择
    # ------------------------------------------------------------
    def _initial_dir(self, key):
        current = self.entries[key].get().strip()
        if current:
            parent = os.path.dirname(current)
            if os.path.isdir(parent):
                return parent
        return str(SCRIPT_DIR)

    def pick_file(self, key):
        import tkinter.filedialog as fd
        filetypes = {
            "MODEL_PATH": [("GGUF model", "*.gguf"), ("All files", "*.*")],
            "MMPROJ_PATH": [("GGUF model", "*.gguf"), ("All files", "*.*")],
            "CHAT_TEMPLATE": [("Jinja template", "*.jinja"), ("All files", "*.*")],
        }.get(key, [("All files", "*.*")])
        path = fd.askopenfilename(
            title="Select File",
            initialdir=self._initial_dir(key),
            filetypes=filetypes,
        )
        if path:
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, path)
            self.append_log(f"{key} selected: {path}")

    def pick_dir(self, key):
        import tkinter.filedialog as fd
        path = fd.askdirectory(
            title="Select Directory",
            initialdir=self._initial_dir(key),
        )
        if path:
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, path)
            self.append_log(f"{key} selected: {path}")

    # ------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------
    def build_ui(self):
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        # 标题栏
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="Local LLM Manager", style="Title.TLabel").pack(side=tk.LEFT)
        self.lock_label = ttk.Label(header, text="", style="Dim.TLabel",
                                    font=(self.ui_font, 10, "bold"),
                                    foreground=COLORS["yellow"])
        self.lock_label.pack(side=tk.RIGHT, padx=(0, 16))
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="StatusIdle.TLabel")
        self.status_label.pack(side=tk.RIGHT)

        # 双卡 GPU 实时状态：每卡一行（文字）
        gpu_box = ttk.Frame(header)
        gpu_box.pack(side=tk.RIGHT, padx=(0, 16))
        self.gpu_labels = []
        for idx in range(2):
            label = ttk.Label(gpu_box, text=f"GPU{idx} --%", style="Dim.TLabel",
                              font=(self.ui_font, 10, "bold"), width=28,
                              anchor="e")
            label.pack(fill=tk.X, pady=2)
            self.gpu_labels.append(label)
        # Token 速度 + 累计 token 数显示
        self.tps_label = ttk.Label(header, text="Speed: -- t/s", style="Dim.TLabel",
                                   font=(self.ui_font, 10, "bold"),
                                   foreground=COLORS["accent"], width=22, anchor="e")
        self.tps_label.pack(side=tk.RIGHT, padx=(0, 16))
        self.tok_label = ttk.Label(header, text="Tokens: 0", style="Dim.TLabel",
                                   font=(self.ui_font, 10, "bold"),
                                   foreground=COLORS["fg_dim"], width=14, anchor="e")
        self.tok_label.pack(side=tk.RIGHT, padx=(0, 16))
        self.url_label = ttk.Label(header, text="", style="Dim.TLabel")
        self.url_label.pack(side=tk.RIGHT, padx=(0, 16))

        # 主体：上方参数（单页滚动），下方日志
        body = ttk.Frame(main)
        body.pack(fill=tk.BOTH, expand=True)

        # 上方：参数（可滚动），固定高度
        top = ttk.Frame(body)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        # 可滚动容器：Canvas + 内部 Frame
        canvas = tk.Canvas(top, bg=COLORS["bg"], highlightthickness=0, bd=0, height=400)
        vsb = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 内部 frame 随 canvas 宽度自适应 + 标题列宽度随窗口宽度自适应缩放
        # 注意：同一个 <Configure> 事件只能绑定一个 handler（后绑定会覆盖先绑定），
        # 所以两项逻辑都放在 _on_canvas_resize 里
        self.title_labels = []
        self.hint_labels = []
        self.group_panels = {}
        self._canvas = canvas
        self._canvas_window = canvas_window
        canvas.bind("<Configure>", self._on_canvas_resize)

        self.build_group(self.scroll_frame, "Basic Parameters", GROUP_BASE)
        self.build_group(self.scroll_frame, "Model Parameters", GROUP_MODEL)
        self.build_group(self.scroll_frame, "Advanced Parameters", GROUP_ADVANCED)

        # 参数区：鼠标指向时滚轮可上下滚动
        self._bind_mouse_wheel(canvas)
        self._bind_mouse_wheel(self.scroll_frame)

        # 下方：按钮行 + 日志（按钮行先打包，避免被日志区挤掉）
        bottom = ttk.Frame(body)
        bottom.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 按钮行（先打包，固定高度，始终可见）
        btn_row = ttk.Frame(bottom)
        btn_row.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        # 合并 Start/Stop 为一个切换按钮
        self.btn_toggle = ttk.Button(btn_row, text="▶  Start", style="Start.TButton",
                                     command=self.toggle_server)
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_save = ttk.Button(btn_row, text="Save Config", command=self.save_config)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_reset = ttk.Button(btn_row, text="Reset Defaults", command=self.reset_defaults)
        self.btn_reset.pack(side=tk.LEFT)

        # 日志卡片（填充按钮行下方的剩余空间）
        log_card = ttk.Frame(bottom, style="Card.TFrame", padding=10)
        log_card.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        log_title_row = ttk.Frame(log_card)
        log_title_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(log_title_row, text="Log Output", style="Card.TLabel",
                  font=(self.ui_font, 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(log_title_row, text="Clear", style="TButton",
                   command=self.clear_log).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(log_title_row, text="Save Log", style="TButton",
                   command=self.save_log).pack(side=tk.RIGHT, padx=(6, 0))

        log_bg = tk.Text(log_card, wrap=tk.WORD, font=(self.mono_font, 10),
                         bg=COLORS["entry_bg"], fg=COLORS["fg"],
                         insertbackground=COLORS["fg"], relief=tk.FLAT,
                         state=tk.DISABLED, padx=8, pady=8)
        log_scroll = ttk.Scrollbar(log_card, command=log_bg.yview)
        log_bg.configure(yscrollcommand=log_scroll.set)
        log_bg.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_area = log_bg
        self._setup_log_tags()

        # 运行中锁定参数：保存所有参数控件引用
        self.param_widgets = []
        self._collect_param_widgets()

        self.root.bind("<Destroy>", self.on_close)

    def _on_canvas_resize(self, event):
        """内部 frame 填满 canvas 宽度；标题列宽度随窗口宽度自适应缩放。"""
        import tkinter.font as tkfont
        width_px = event.width
        if width_px < 80:
            return
        # 内部 frame 宽度 = canvas 宽度（占满整个界面宽度）
        try:
            self._canvas.itemconfigure(self._canvas_window, width=width_px)
        except tk.TclError:
            pass
        # 标题列约占参数区宽度的 45%
        target_px = int(width_px * 0.45)
        try:
            font = tkfont.Font(family=self.ui_font, size=10, weight="bold")
            sample = "TheQuickBrownFox"
            avg = font.measure(sample) / len(sample)
            if avg <= 0:
                return
            chars = int(target_px / avg)
        except Exception:
            chars = 38
        chars = max(16, min(chars, 64))
        labels = getattr(self, "title_labels", []) + getattr(self, "hint_labels", [])
        for label in labels:
            try:
                label.configure(width=chars)
            except tk.TclError:
                pass

    def _bind_mouse_wheel(self, widget):
        """递归绑定鼠标滚轮事件：canvas 及其所有子控件（输入框/按钮/标签）。
        光标停在参数区任意控件上时，滚轮都能让设置区上下滚动。"""
        widget.bind("<MouseWheel>", self._on_mouse_wheel)   # Windows / macOS
        widget.bind("<Button-4>", self._on_mouse_wheel)     # Linux 上滚
        widget.bind("<Button-5>", self._on_mouse_wheel)     # Linux 下滚
        for child in widget.winfo_children():
            self._bind_mouse_wheel(child)

    def _on_mouse_wheel(self, event):
        """滚轮事件：滚动参数区 canvas（仅当内容超出可视区域时）。"""
        canvas = getattr(self, "_canvas", None)
        if canvas is None:
            return
        try:
            if not canvas.winfo_exists():
                return
            # 内容未超出可视区域则不滚动
            bbox = canvas.bbox("all")
            if not bbox or bbox[3] <= canvas.winfo_height():
                return
        except tk.TclError:
            return
        # 计算滚动方向：Windows/macOS 用 delta（正=上滚，负=下滚），Linux 用 num（4 上 / 5 下）
        # 下滚应让视图往下移（yview 增大）→ yview_scroll(+n)；上滚反之（-n）
        delta = getattr(event, "delta", 0)
        if delta:
            down = delta < 0
        else:
            n = getattr(event, "num", 0)
            if n == 4:          # 上滚
                down = False
            elif n == 5:        # 下滚
                down = True
            else:
                return
        # 每次滚 3「单位」（canvas 单位 = 视口高度的 1/10），平滑且跟手
        canvas.yview_scroll(3 if down else -3, "units")

    def _collect_param_widgets(self):
        """递归收集所有参数输入框与「选择」按钮，用于运行时禁用。"""
        self.param_widgets = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Button)):
                    self.param_widgets.append(child)
                walk(child)

        walk(self.scroll_frame)

    def _title_width(self):
        """计算所有参数标题的最大长度，用于统一列宽。"""
        all_fields = GROUP_BASE + GROUP_MODEL + GROUP_ADVANCED
        longest = 0
        for key, label, _hint in all_fields:
            longest = max(longest, len(f"{label}  ({key})"))
        return longest + 2  # 留一点余量

    def build_group(self, parent, title, fields):
        col_width = self._title_width()
        # 分组卡片：上下排列、占满宽度，点击标题可折叠/展开
        card = ttk.Frame(parent, style="Card.TFrame", padding=(8, 4))
        card.pack(fill=tk.X, pady=(10, 2))

        # 可点击的分组标题（点击切换展开/收起）
        header = ttk.Label(card, text=f"▼ {title}", style="Card.TLabel",
                           font=(self.ui_font, 11, "bold"), foreground=COLORS["accent"],
                           anchor="w", cursor="hand2")
        header.pack(anchor="w", fill=tk.X)
        header.bind("<Button-1>", lambda e: self._toggle_group(title))

        # 内容区（折叠时隐藏）
        content = ttk.Frame(card, style="Card.TFrame")
        content.pack(fill=tk.X, pady=(4, 0))

        self.group_panels[title] = {"header": header, "content": content,
                                    "expanded": True}

        # 需要文件选择器的参数
        file_pickers = {"MODEL_PATH", "MMPROJ_PATH", "CHAT_TEMPLATE"}
        # 需要目录选择器的参数
        dir_pickers = {"LLAMA_DIR"}

        for key, label, hint in fields:
            row = ttk.Frame(content, style="Card.TFrame", padding=8)
            row.pack(fill=tk.X, pady=4)
            # 标题列统一宽度，保证各行输入框左对齐（宽度随窗口自适应）
            left_col = ttk.Frame(row)
            left_col.pack(side=tk.LEFT)
            title_label = ttk.Label(left_col, text=f"{label}  ({key})", style="Card.TLabel",
                                    font=(self.ui_font, 10, "bold"), anchor="w",
                                    width=col_width)
            title_label.pack(anchor="w")
            self.title_labels.append(title_label)
            hint_label = ttk.Label(left_col, text=hint, style="Card.TLabel",
                                   foreground=COLORS["fg_dim"], anchor="w",
                                   width=col_width)
            hint_label.pack(anchor="w")
            self.hint_labels.append(hint_label)

            entry = ttk.Entry(row, font=(self.mono_font, 10))
            entry.insert(0, str(self.config.get(key, "")))
            entry.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(12, 0))
            self.entries[key] = entry

            # Add a "Browse" button for path parameters
            if key in file_pickers:
                ttk.Button(row, text="Browse…", width=8,
                           command=lambda k=key: self.pick_file(k)).pack(
                    side=tk.RIGHT, padx=(8, 0))
            elif key in dir_pickers:
                ttk.Button(row, text="Browse…", width=8,
                           command=lambda k=key: self.pick_dir(k)).pack(
                    side=tk.RIGHT, padx=(8, 0))

    def _toggle_group(self, title):
        """点击分组标题，切换该分组的展开/收起状态。"""
        panel = self.group_panels.get(title)
        if not panel:
            return
        if panel["expanded"]:
            panel["content"].pack_forget()
            panel["header"].configure(text=f"▶ {title}")
            panel["expanded"] = False
        else:
            panel["content"].pack(fill=tk.X, pady=(4, 0))
            panel["header"].configure(text=f"▼ {title}")
            panel["expanded"] = True

    # ------------------------------------------------------------
    # 日志（按级别着色）
    # ------------------------------------------------------------
    def _setup_log_tags(self):
        c = COLORS
        # tag: (前景色, 是否加粗)
        self.log_area.tag_configure("error", foreground=c["red"],
                                   font=(self.mono_font, 10, "bold"))
        self.log_area.tag_configure("ready", foreground=c["green"],
                                   font=(self.mono_font, 10, "bold"))
        self.log_area.tag_configure("warn", foreground=c["yellow"])
        self.log_area.tag_configure("cmd", foreground=c["accent"])
        self.log_area.tag_configure("info", foreground=c["fg"])

    def _classify(self, message):
        m = message.lower()
        if any(k in m for k in ("error", "not found", "does not exist", "failed")):
            return "error"
        if any(k in m for k in ("ready", "running", "stopped", "done", "exited")):
            return "ready"
        if any(k in m for k in ("warn", "timeout")):
            return "warn"
        if m.startswith(("command:", "preparing", "starting")):
            return "cmd"
        return "info"

    def append_log(self, message):
        def _do():
            if self.closing:
                return
            tag = self._classify(message)
            self.log_area.configure(state=tk.NORMAL)
            ts = time.strftime("%H:%M:%S")
            self.log_area.insert(tk.END, f"{ts}  ", "info")
            self.log_area.insert(tk.END, f"{message}\n", tag)
            self.log_area.see(tk.END)
            self.log_area.configure(state=tk.DISABLED)
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

    def set_status(self, text, style):
        self.status_var.set(text)
        self.status_label.configure(style=style)

    def set_params_editable(self, editable):
        """运行时锁定参数：禁用所有参数输入框与选择按钮，并显示提示。"""
        state = "normal" if editable else "disabled"
        for widget in self.param_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        try:
            if editable:
                self.lock_label.configure(text="")
            else:
                self.lock_label.configure(text="🔒 Parameters locked while running")
        except tk.TclError:
            pass

    # ------------------------------------------------------------
    # 日志：清空 / 保存
    # ------------------------------------------------------------
    def clear_log(self):
        self.log_area.configure(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.log_area.configure(state=tk.DISABLED)
        self.append_log("Log cleared.")

    def save_log(self):
        import tkinter.filedialog as fd
        default_name = f"llm_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path = fd.asksaveasfilename(
            defaultextension=".txt",
            initialfile=default_name,
            initialdir=str(SCRIPT_DIR),
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
            title="Save Log To File",
        )
        if not path:
            return
        try:
            content = self.log_area.get(1.0, tk.END)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self.append_log(f"Log saved to: {path}")
        except Exception as exc:
            self.append_log(f"Failed to save log: {exc}")

    # ------------------------------------------------------------
    # GPU 占用轮询（nvidia-smi）
    # ------------------------------------------------------------
    def poll_gpu(self):
        if self.closing:
            return
        # 查询所有 GPU：名称 + 占用率 + 显存
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0:
                lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
                for idx in range(2):
                    if idx < len(lines):
                        parts = [p.strip() for p in lines[idx].split(",")]
                        if len(parts) >= 4:
                            util = int(parts[1])
                            used, total = parts[2], parts[3]
                            self.gpu_labels[idx].configure(
                                text=f"GPU{idx} {util}%  {used}/{total}MB"
                            )
                    else:
                        # GPU not present
                        self.gpu_labels[idx].configure(text=f"GPU{idx} not detected")
            else:
                self._gpu_unavailable()
        except Exception:
            self._gpu_unavailable()
        self.root.after(3000, self.poll_gpu)

    def _gpu_unavailable(self):
        for idx in range(2):
            self.gpu_labels[idx].configure(text=f"GPU{idx} N/A")

    # ------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------
    def validate(self):
        values = {k: self.entries[k].get().strip() for k in DEFAULT_VALUES}
        # 必填：llama-server 所在目录 + 模型文件
        for key, label in [("LLAMA_DIR", "llama dir"), ("MODEL_PATH", "Model")]:
            if not values[key]:
                raise ValueError(f"{label} cannot be empty")
            if not os.path.exists(values[key]):
                raise FileNotFoundError(f"{label} not found: {values[key]}")
        # 可选：填写了才校验存在
        for key, label in [("MMPROJ_PATH", "Vision Proj"), ("CHAT_TEMPLATE", "Chat Template")]:
            if values[key] and not os.path.exists(values[key]):
                raise FileNotFoundError(f"{label} not found: {values[key]}")
        exe = os.path.join(values["LLAMA_DIR"], "llama-server.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(f"llama-server.exe not found: {exe}")
        if not values["PORT"].isdigit():
            raise ValueError(f"Port must be a number: {values['PORT']}")
        for key, label in [("CTX_SIZE", "Context Size"), ("THREADS", "Threads"),
                           ("TBATCH", "Thread Batch"), ("BATCH", "Batch"),
                           ("UBATCH", "UBatch")]:
            if not values[key].isdigit():
                raise ValueError(f"{label} must be a number: {values[key]}")
        return values

    def build_command(self, v):
        cmd = [
            os.path.join(v["LLAMA_DIR"], "llama-server.exe"),
            "-m", v["MODEL_PATH"],
            "-t", v["THREADS"],
            "-tb", v["TBATCH"],
            "-b", v["BATCH"],
            "-ub", v["UBATCH"],
            "-c", v["CTX_SIZE"],
            "--temp", v["TEMPERATURE"],
            "--host", v["HOST"],
            "--port", v["PORT"],
            "-ngl", "999",
            "-ctk", "q8_0",
            "-ctv", "q8_0",
            "--parallel", "1",
            "--kv-unified",
            "--flash-attn", "on",
            "-sm", "tensor",
        ]
        # 以下参数仅在填写时加入，保持通用性
        if v["MMPROJ_PATH"]:
            cmd += ["--mmproj", v["MMPROJ_PATH"]]
        if v["CHAT_TEMPLATE"]:
            cmd += ["--chat-template-file", v["CHAT_TEMPLATE"]]
        if v["ALIAS"]:
            cmd += ["--alias", v["ALIAS"]]
        if v["TENSOR_SPLIT"]:
            cmd += ["--tensor-split", v["TENSOR_SPLIT"]]
        if v["SPEC_TYPE"]:
            cmd += ["--spec-type", v["SPEC_TYPE"]]
        if v["SPEC_DRAFT_N_MAX"]:
            cmd += ["--spec-draft-n-max", v["SPEC_DRAFT_N_MAX"]]
        if v["SPEC_DRAFT_P_MIN"]:
            cmd += ["--spec-draft-p-min", v["SPEC_DRAFT_P_MIN"]]
        if v["API_KEY"]:
            cmd += ["--api-key", v["API_KEY"]]
        return cmd

    def start_server(self):
        if self.process is not None and self.process.poll() is None:
            self.append_log("Server is already running. No need to start again.")
            return
        try:
            v = self.validate()
            self.save_config()
            cmd = self.build_command(v)
            self.append_log("Preparing to start llama-server ...")
            self.append_log("Command: " + " ".join(cmd))
            self.url_label.configure(text=f"URL: http://{v['CHECK_HOST']}:{v['PORT']}")

            self.process = subprocess.Popen(
                cmd,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.server_ready = False
            self.set_status("● Starting", "StatusStart.TLabel")
            self.set_params_editable(False)
            self._update_toggle_button(running=True)
            self.btn_save.configure(state="disabled")
            self.btn_reset.configure(state="disabled")
            threading.Thread(target=self._read_output, daemon=True).start()
            self.root.after(1000, self.poll_health)
        except (ValueError, FileNotFoundError) as exc:
            self.append_log(f"Start failed: {exc}")

    def _tps_color(self, value):
        # 速度越快越绿，越慢越红（0~100 t/s 区间内插值，白底用深色）
        t = max(0.0, min(1.0, value / 100.0))
        # 深红 (cc0000) -> 深绿 (1a7f37)
        r = int(0xcc + (0x1a - 0xcc) * t)
        g = int(0x00 + (0x7f - 0x00) * t)
        b = int(0x00 + (0x37 - 0x00) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _parse_tps(self, line):
        # 匹配 llama-server 日志里的 token 速度，例如:
        #   tg_3s =  72.23 t/s   /   3.2s   120 tokens   37.5 t/s
        def _do():
            m = re.search(r'([\d.]+)\s*t/s', line)
            if m:
                value = float(m.group(1))
                color = self._tps_color(value)
                self.tps_label.configure(text=f"Speed: {value:.1f} t/s",
                                         foreground=color)
            # 累加 token 数（同一行或单独行）
            mt = re.search(r'(\d+)\s*tokens', line)
            if mt:
                self.total_tokens += int(mt.group(1))
                self.tok_label.configure(text=f"Tokens: {self.total_tokens}")

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

    def _read_output(self):
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            self.append_log(line.rstrip())
            self._parse_tps(line)
        if self.process is proc:
            code = proc.poll()
            if code is not None:
                self.append_log(f"Process exited with code: {code}")
                self.set_status("● Stopped", "StatusIdle.TLabel")
                self.tps_label.configure(text="Speed: -- t/s",
                                         foreground=COLORS["accent"])
                self.total_tokens = 0
                self.tok_label.configure(text="Tokens: 0")
                self.set_params_editable(True)
                self._update_toggle_button(running=False)
                self.btn_save.configure(state="normal")
                self.btn_reset.configure(state="normal")
                self.process = None

    def poll_health(self):
        if self.closing:
            return
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        host = self.entries["CHECK_HOST"].get().strip() or "127.0.0.1"
        port = self.entries["PORT"].get().strip()
        url = f"http://{host}:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    self.server_ready = True
                    self.set_status("● Running", "StatusRun.TLabel")
                    self.append_log(f"Server ready: {url}")
                    return
        except Exception:
            pass
        self.root.after(1500, self.poll_health)

    def refresh_status(self):
        host = self.entries["CHECK_HOST"].get().strip() or "127.0.0.1"
        port = self.entries["PORT"].get().strip() or "8080"
        url = f"http://{host}:{port}/health"
        if self.process is not None and self.process.poll() is None:
            return
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    self.set_status("● Running (external)", "StatusRun.TLabel")
                    self.url_label.configure(text=f"URL: http://{host}:{port}")
                    return
        except Exception:
            pass
        self.set_status("● Stopped", "StatusIdle.TLabel")

    def toggle_server(self):
        """合并 Start/Stop：运行中则停止，否则启动。"""
        if self.process is not None and self.process.poll() is None:
            self.stop_server()
        else:
            self.start_server()

    def _update_toggle_button(self, running):
        """根据运行状态更新切换按钮的文字与样式。"""
        try:
            if running:
                self.btn_toggle.configure(text="■  Stop", style="Stop.TButton")
            else:
                self.btn_toggle.configure(text="▶  Start", style="Start.TButton")
        except tk.TclError:
            pass

    def stop_server(self):
        if self.process is None:
            self.append_log("No server process is currently managed by the GUI.")
            return
        # 确认对话框，防止误点
        confirm = messagebox.askyesno(
            "Stop Server",
            "Stop the model server?\n\nParameters will be unlocked after stopping.",
            icon="warning",
        )
        if not confirm:
            self.append_log("Stop cancelled by user.")
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.append_log("Server stopped.")
        except subprocess.TimeoutExpired:
            self.append_log("Stop timed out. Force-killing the process.")
            self.process.kill()
        finally:
            self.process = None
            self.server_ready = False
            self.set_status("● Stopped", "StatusIdle.TLabel")
            self.set_params_editable(True)
            self._update_toggle_button(running=False)
            self.btn_save.configure(state="normal")
            self.btn_reset.configure(state="normal")

    def on_close(self, event=None):
        if self.closing:
            return
        self.closing = True
        self.save_config()
        if self.process is not None:
            try:
                self.process.terminate()
            except Exception:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = LLMManagerGUI(root)
    root.mainloop()
