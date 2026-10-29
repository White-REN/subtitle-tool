"""Subtitle Tool - desktop UI.

结构参考 Windows 官方应用（NavigationView + 设置卡片），
视觉语言保持不变：近黑底、荧光绿强调、等宽字体微标签、方括号编号。
"""

import ctypes
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox


# ============================================================
# Theme
# ============================================================

BG = "#0b0b0b"            # 窗口内容底
SIDEBAR = "#0d0d0d"       # 左侧导航
LAYER = "#171717"         # 卡片（靠明度差与底色分离，不描边）
LAYER_ALT = "#1f1f1f"     # 卡片内的次级层 / hover
STROKE = "#2c2c2c"        # 控件描边（按钮 / 分段 / 徽章）
STROKE_SOFT = "#232323"   # 分隔线

TEXT = "#ededed"
TEXT_2 = "#9b9b9b"
TEXT_3 = "#666666"

ACCENT = "#d9ff64"
ACCENT_DIM = "#55611f"
DANGER = "#ff6b6b"
TRACK = "#262626"

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

APP_TITLE = "Subtitle Tool"
APP_VERSION = "1.1.0"

RADIUS = 10       # 卡片
RADIUS_CTRL = 6   # 控件

PAGE_PAD = 34
SIDEBAR_W = 208


# ============================================================
# Paths
# ============================================================

import models_setup
import paths

ROOT = paths.RESOURCE_DIR
MAIN_PY = paths.script_path("main.py")
LOGO_PATH = paths.LOGO_PATH
CONFIG_FILE = paths.CONFIG_FILE

APP_DATA = paths.APP_DATA
MODEL_DIR = paths.USER_MODEL_DIR


# ============================================================
# Helpers
# ============================================================

def hex_to_rgb(value):
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(v) for v in rgb))


def mix(color_a, color_b, t):
    a = hex_to_rgb(color_a)
    b = hex_to_rgb(color_b)
    return rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def cancel_after(widget, *attrs):
    """取消挂在控件上的 after 回调，避免控件销毁后 Tk 报 invalid command name。"""
    for attr in attrs:
        ident = getattr(widget, attr, None)
        if ident is None:
            continue
        try:
            widget.after_cancel(ident)
        except tk.TclError:
            pass
        setattr(widget, attr, None)


def boost_timer_resolution():
    """把 Windows 定时器精度提到 1ms。

    默认精度是 15.6ms，Tk 的 after(8) 实际要等 15.6ms 才触发，
    所有补间都会被量化成 ~64Hz 的抖动。提到 1ms 后 after 才能真正
    按毫秒触发，动画才谈得上顺滑。
    """
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.winmm.timeBeginPeriod(1) == 0
    except Exception:
        return False


def release_timer_resolution():
    if os.name != "nt":
        return
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def cubic_bezier(p1x, p1y, p2x, p2y, samples=14):
    """把 CSS cubic-bezier 转成可用的缓动函数。"""
    def curve(t, a, b):
        u = 1.0 - t
        return 3 * u * u * t * a + 3 * u * t * t * b + t ** 3

    def solve(x):
        x = max(0.0, min(1.0, x))
        low, high = 0.0, 1.0
        for _ in range(samples):
            mid = (low + high) / 2.0
            if curve(mid, p1x, p2x) < x:
                low = mid
            else:
                high = mid
        return curve((low + high) / 2.0, p1y, p2y)

    return solve


# Fluent 标准缓动 cubic-bezier(0.1, 0.9, 0.2, 1.0)：起步快、收尾长，适合状态变化
ease_out = cubic_bezier(0.1, 0.9, 0.2, 1.0)

# 位移类动画用两端缓：短距离移动如果也前倾，会像"瞬移 + 卡住"
ease_in_out = cubic_bezier(0.4, 0.0, 0.2, 1.0)

# 长距离位移：起步就有速度但不过冲，收尾干净。比 ease_out 平缓，
# 比 ease_in_out 果断，单帧最大跳变约为行程的 1/6。
ease_soft = cubic_bezier(0.25, 0.75, 0.25, 1.0)


def run_tween(widget, duration, on_frame, attr="_anim", interval=8, curve=None):
    """按真实时间推进的补间，on_frame 收到 0..1 的缓动后进度。

    用 time.perf_counter() 计时而不是数步数：Tk 的 after 在 Windows 上
    有 ~15ms 抖动，按步数推进会一顿一顿的。
    """
    cancel_after(widget, attr)

    ease = curve or ease_out
    start = time.perf_counter()
    total = max(1.0, float(duration)) / 1000.0

    def step():
        try:
            if not widget.winfo_exists():
                return
        except tk.TclError:
            return

        progress = min(1.0, (time.perf_counter() - start) / total)

        try:
            on_frame(ease(progress))
        except tk.TclError:
            return

        if progress < 1.0:
            try:
                setattr(widget, attr, widget.after(interval, step))
            except tk.TclError:
                setattr(widget, attr, None)
        else:
            setattr(widget, attr, None)

    try:
        setattr(widget, attr, widget.after(interval, step))
    except tk.TclError:
        setattr(widget, attr, None)


def round_rect_points(x1, y1, x2, y2, radius, steps=None):
    """解析采样圆角矩形的闭合顶点序列（顺时针）。"""
    radius = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))

    if radius <= 0.01:
        return [x1, y1, x2, y1, x2, y2, x1, y2]

    steps = steps or max(10, int(radius * 3))

    corners = (
        (x2 - radius, y1 + radius, -90.0, 0.0),     # 右上
        (x2 - radius, y2 - radius, 0.0, 90.0),      # 右下
        (x1 + radius, y2 - radius, 90.0, 180.0),    # 左下
        (x1 + radius, y1 + radius, 180.0, 270.0),   # 左上
    )

    points = []
    for cx, cy, start, end in corners:
        for index in range(steps + 1):
            angle = math.radians(start + (end - start) * index / steps)
            points.append(cx + radius * math.cos(angle))
            points.append(cy + radius * math.sin(angle))

    return points


def round_rect(canvas, x1, y1, x2, y2, radius, fill="", outline="",
               width=1, tags=()):
    """圆角矩形。

    边框不画折线，而是「外框实心填充 + 内层实心填充」两层叠出来：
    Tk 没有抗锯齿，1px 折线在浅角度的斜边上会一段一段断开（就是那些
    虚线），而实心填充是逐像素覆盖的，绝不会断。
    """
    if outline and fill and width:
        canvas.create_polygon(
            round_rect_points(x1, y1, x2, y2, radius),
            fill=outline, outline="", tags=tags,
        )
        inset = float(width)
        canvas.create_polygon(
            round_rect_points(
                x1 + inset, y1 + inset, x2 - inset, y2 - inset,
                max(0.0, radius - inset),
            ),
            fill=fill, outline="", tags=tags,
        )
        return

    if fill:
        canvas.create_polygon(
            round_rect_points(x1, y1, x2, y2, radius),
            fill=fill, outline="", tags=tags,
        )
        return

    if outline and width:
        points = round_rect_points(x1, y1, x2, y2, radius)
        canvas.create_line(
            points + points[:2],
            fill=outline, width=width,
            capstyle="round", joinstyle="round",
            tags=tags,
        )


def human_size_bytes(size):
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def human_size(path):
    try:
        size = float(path.stat().st_size)
    except OSError:
        return str(path)

    return human_size_bytes(size)


def format_eta(seconds):
    if seconds <= 0 or seconds > 86400:
        return "--"
    minutes, sec = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分 {sec} 秒"


def explain_failure(lines):
    """把子进程输出翻译成一句人话。

    流水线脚本失败时会往 stdout/stderr 打一整段 traceback，直接把最后几行丢给
    用户看没有意义（"进程退出，代码 1"）。这里优先匹配已知的失败原因，退而求其次
    才去找最后一条异常行。完整堆栈仍然留在日志区里。
    """
    joined = "\n".join(lines)

    for needle, hint in FAILURE_HINTS:
        if needle in joined:
            return hint

    for line in reversed(lines):
        stripped = line.strip()
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception)\b", stripped):
            return stripped[:200]

    for line in reversed(lines):
        if line.strip():
            return line.strip()[:200]

    return "任务异常结束，详情见日志区。"


FAILURE_HINTS = [
    (
        "没有检测到语音区域",
        "这段视频没有检测到人声。可能全程无对白，或者音轨是纯音乐 / 静音。",
    ),
    (
        "找不到翻译模型",
        "翻译模型还没安装。到「字幕翻译」页，把模型文件放到提示的目录即可。",
    ),
    (
        "找不到配置文件",
        "配置文件缺失。请重新运行安装包修复。",
    ),
    (
        "ModuleNotFoundError",
        "程序文件不完整，可能安装过程被中断。请重新运行安装包。",
    ),
    (
        "ImportError",
        "程序文件不完整，可能安装过程被中断。请重新运行安装包。",
    ),
    (
        "没有找到字幕文件",
        "字幕没有生成成功，请查看日志区上方的报错。",
    ),
    (
        "没有找到 MKV",
        "视频封装失败，请确认磁盘空间是否充足。",
    ),
]


def darken_titlebar(window):
    """Windows 10/11 深色标题栏，避免白条破坏整体配色。"""
    if os.name != "nt":
        return

    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)

        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
    except Exception:
        pass


# ============================================================
# Card
# ============================================================

class Card(tk.Frame):
    """圆角卡片：Canvas 画底，Frame 承载内容。"""

    def __init__(self, parent, radius=RADIUS, fill=LAYER, outline="",
                 padx=4, pady=4, bg=None):
        page_bg = bg or parent.cget("bg")

        super().__init__(parent, bg=page_bg, highlightthickness=0, bd=0)

        self.radius = radius
        self.fill = fill
        self.outline = outline

        self.canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=padx, pady=pady)

        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 2 or height <= 2:
            return

        self.canvas.delete("card")
        round_rect(
            self.canvas, 0, 0, width, height,
            self.radius, fill=self.fill, outline=self.outline, width=1, tags="card",
        )

    def set_outline(self, color):
        if color == self.outline:
            return
        self.outline = color
        self._redraw()


# ============================================================
# Button
# ============================================================

class Btn(tk.Canvas):
    """Fluent 风格按钮：6px 圆角。"""

    STYLES = {
        "accent": {
            "fill": ACCENT, "fg": "#0a0a0a", "border": "",
            "hfill": "#e9ffa6", "hfg": "#0a0a0a", "hborder": "",
            "pfill": "#b6d94a",
        },
        "standard": {
            "fill": LAYER_ALT, "fg": TEXT, "border": STROKE,
            "hfill": "#232323", "hfg": TEXT, "hborder": "#3a3a3a",
            "pfill": "#2b2b2b",
        },
        "subtle": {
            "fill": None, "fg": TEXT_2, "border": "",
            "hfill": LAYER_ALT, "hfg": TEXT, "hborder": "",
            "pfill": "#252525",
        },
    }

    def __init__(self, parent, text, command=None, style="standard",
                 height=32, padx=14, width=None, font=None, bg=None):
        self.page_bg = bg or parent.cget("bg")
        self.style = style
        self.command = command
        self.padx = padx
        self.height = height
        self.font_spec = font or (FONT_UI, 9)
        self._text = text
        self._enabled = True
        self._t = 0.0
        self._press = 0.0
        self._anim = None
        self._press_anim = None
        self._fixed_width = width

        self._font = tkfont.Font(
            family=self.font_spec[0],
            size=self.font_spec[1],
            weight=self.font_spec[2] if len(self.font_spec) > 2 else "normal",
        )

        super().__init__(
            parent,
            width=width or self._auto_width(),
            height=height,
            bg=self.page_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )

        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._animate(1.0))
        self.bind("<Leave>", lambda _e: self._animate(0.0))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _auto_width(self):
        return max(64, self._font.measure(self._text) + self.padx * 2)

    def _paint(self, t=None, press=None):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        if t is None:
            t = self._t
        if press is None:
            press = self._press

        self.delete("all")

        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1:
            width = int(self["width"])
        if height <= 1:
            height = self.height

        spec = self.STYLES[self.style]

        if not self._enabled:
            fill, fg, border = LAYER_ALT, TEXT_3, STROKE
        else:
            base_fill = spec["fill"] or self.page_bg
            hover_fill = spec["hfill"] or self.page_bg
            fill = mix(base_fill, hover_fill, t)
            fill = mix(fill, spec["pfill"], press)
            fg = mix(spec["fg"], spec["hfg"], t)
            border = mix(spec["border"], spec["hborder"], t) if spec["border"] else ""

        round_rect(
            self, 0, 0, width, height,
            RADIUS_CTRL, fill=fill, outline=border, width=1,
        )

        self.create_text(
            width / 2, height / 2 + 1,
            text=self._text, fill=fg, font=self.font_spec,
        )

    def _animate(self, target):
        if not self._enabled:
            return

        start = self._t

        def frame(p):
            self._t = start + (target - start) * p
            self._paint()

        run_tween(self, 220, frame, attr="_anim")

    def _animate_press(self, target, duration=150):
        start = self._press

        def frame(p):
            self._press = start + (target - start) * p
            self._paint()

        run_tween(self, duration, frame, attr="_press_anim")

    def _on_press(self, _event=None):
        if self._enabled:
            self._animate_press(1.0, duration=110)

    def _on_release(self, _event=None):
        if not self._enabled:
            return

        self._animate_press(0.0, duration=280)

        if self.command is not None:
            self.command()

    def destroy(self):
        cancel_after(self, "_anim", "_press_anim")
        super().destroy()

    def set_state(self, state):
        self._enabled = state != "disabled"
        try:
            self.configure(cursor="hand2" if self._enabled else "")
        except tk.TclError:
            return
        self._paint(self._t)

    def set_text(self, text):
        self._text = text
        try:
            self.configure(width=self._fixed_width or self._auto_width())
        except tk.TclError:
            return
        self._paint(self._t)

    def set_style(self, style):
        if style == self.style:
            return
        self.style = style
        self._t = 0.0
        self._paint(0.0)


# ============================================================
# Chip
# ============================================================

class Chip(tk.Canvas):
    """小徽章：4px 圆角、1px 描边。"""

    def __init__(self, parent, text="", fg=TEXT_3, fill=None, border=STROKE,
                 height=22, padx=9, bg=None, font=(FONT_MONO, 8, "bold")):
        self.page_bg = bg or parent.cget("bg")
        self._text = text
        self._fg = fg
        self._fill = fill
        self._border = border
        self.height = height
        self.padx = padx
        self.font_spec = font

        self._font = tkfont.Font(
            family=font[0], size=font[1],
            weight=font[2] if len(font) > 2 else "normal",
        )

        super().__init__(
            parent, width=self._width(), height=height,
            bg=self.page_bg, highlightthickness=0, bd=0,
        )
        self.bind("<Configure>", lambda _e: self._paint())

    def _width(self):
        return max(30, self._font.measure(self._text) + self.padx * 2)

    def _paint(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        self.delete("all")

        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1:
            width = self._width()
        if height <= 1:
            height = self.height

        round_rect(
            self, 0, 0, width, height, RADIUS_CTRL,
            fill=self._fill or self.page_bg, outline=self._border, width=1,
        )
        self.create_text(
            width / 2, height / 2 + 1,
            text=self._text, fill=self._fg, font=self.font_spec,
        )

    def set(self, text, fg=TEXT_3, fill=None, border=STROKE):
        self._text = text
        self._fg = fg
        self._fill = fill
        self._border = border
        try:
            self.configure(width=self._width())
        except tk.TclError:
            return
        self._paint()


# ============================================================
# Progress bar
# ============================================================

class Bar(tk.Canvas):
    def __init__(self, parent, height=4, bg=None):
        super().__init__(
            parent, height=height, bg=bg or parent.cget("bg"),
            highlightthickness=0, bd=0,
        )
        self._h = height
        self._value = 0.0
        self._anim = None
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, value):
        try:
            value = max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return

        cancel_after(self, "_anim")

        if abs(self._value - value) < 0.5:
            self._value = value
            self._draw()
            return

        start = self._value

        def frame(p):
            self._value = start + (value - start) * p
            self._draw()

        run_tween(self, 420, frame, attr="_anim")

    def destroy(self):
        cancel_after(self, "_anim")
        super().destroy()

    def _draw(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        self.delete("all")

        width = self.winfo_width()
        if width <= 1:
            return

        radius = self._h / 2
        round_rect(self, 0, 0, width, self._h, radius, fill=TRACK, outline="")

        filled = width * self._value / 100
        if filled > 0:
            filled = max(filled, self._h)
            round_rect(self, 0, 0, filled, self._h, radius, fill=ACCENT, outline="")


# ============================================================
# Segmented
# ============================================================

class Segmented(tk.Canvas):
    def __init__(self, parent, options, value, command=None,
                 height=32, width=220, bg=None):
        self.page_bg = bg or parent.cget("bg")
        self.options = options
        self.value = value
        self.command = command
        self._h = height
        self._anim = None

        values = [option[0] for option in options]
        self._pos = float(values.index(value)) if value in values else 0.0

        super().__init__(
            parent, width=width, height=height, bg=self.page_bg,
            highlightthickness=0, bd=0, cursor="hand2",
        )

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)

    def set(self, value):
        values = [option[0] for option in self.options]
        index = values.index(value) if value in values else 0
        self.value = value

        if abs(self._pos - index) > 0.01:
            self._slide_to(index)
        else:
            self._draw()

    def _slide_to(self, target):
        start = self._pos

        def frame(p):
            self._pos = start + (target - start) * p
            self._draw()

        run_tween(self, 320, frame, attr="_anim")

    def destroy(self):
        cancel_after(self, "_anim")
        super().destroy()

    def _click(self, event):
        width = self.winfo_width()
        if width <= 1 or not self.options:
            return

        count = len(self.options)
        index = min(count - 1, max(0, int(event.x / (width / count))))
        value = self.options[index][0]

        if value == self.value:
            return

        self.value = value
        self._slide_to(index)

        if self.command is not None:
            self.command(value)

    def _draw(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        self.delete("all")

        width = self.winfo_width()
        height = self._h
        if width <= 1:
            return

        round_rect(self, 0, 0, width, height,
                   RADIUS_CTRL, fill=BG, outline=STROKE, width=1)

        count = len(self.options)
        segment = width / count
        values = [option[0] for option in self.options]
        index = values.index(self.value) if self.value in values else 0

        pad = 3
        left = self._pos * segment + pad
        right = (self._pos + 1) * segment - pad
        round_rect(
            self,
            left, pad, right, height - pad,
            RADIUS_CTRL - 1,
            fill=ACCENT, outline="",
        )

        for i, (_value, label) in enumerate(self.options):
            self.create_text(
                i * segment + segment / 2, height / 2 + 1,
                text=label,
                fill="#0a0a0a" if i == index else TEXT_2,
                font=(FONT_MONO, 8, "bold"),
            )


# ============================================================
# Status
# ============================================================

class StatusDot(tk.Frame):
    COLORS = {"ready": TEXT_3, "running": ACCENT, "done": ACCENT, "error": DANGER}
    LABELS = {"ready": "READY", "running": "RUNNING", "done": "DONE", "error": "ERROR"}

    def __init__(self, parent, bg=None):
        page_bg = bg or parent.cget("bg")

        super().__init__(parent, bg=page_bg)

        self.page_bg = page_bg
        self._pulse = None
        self._bright = True

        self.canvas = tk.Canvas(self, width=8, height=8, bg=page_bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack(side="left")

        self.dot = self.canvas.create_oval(0, 0, 7, 7, fill=TEXT_3, outline="")

        self.label = tk.Label(
            self, text="READY", bg=page_bg, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"),
        )
        self.label.pack(side="left", padx=(8, 0))

    def set(self, state, text=None):
        if self._pulse is not None:
            try:
                self.after_cancel(self._pulse)
            except tk.TclError:
                pass
            self._pulse = None

        color = self.COLORS.get(state, TEXT_3)

        try:
            self.canvas.itemconfigure(self.dot, fill=color)
            self.label.configure(
                text=text or self.LABELS.get(state, "READY"),
                fg=color,
            )
        except tk.TclError:
            return

        if state == "running":
            self._bright = True
            self._pulse_loop()

    def _pulse_loop(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        self._bright = not self._bright

        try:
            self.canvas.itemconfigure(
                self.dot, fill=ACCENT if self._bright else ACCENT_DIM,
            )
        except tk.TclError:
            return

        self._pulse = self.after(620, self._pulse_loop)


# ============================================================
# Navigation item
# ============================================================

class NavItem(tk.Frame):
    def __init__(self, parent, index, title, command, bg=SIDEBAR):
        super().__init__(parent, bg=bg, height=40, cursor="hand2")
        self.pack_propagate(False)

        self.command = command
        self.base_bg = bg
        self.active = False

        self._t = 0.0          # 选中进度 0..1
        self._hv = 0.0         # 悬停进度 0..1
        self._t_anim = None
        self._hv_anim = None

        self.indicator = tk.Frame(self, width=3, bg=ACCENT)
        self.indicator.place(x=0, y=20, width=3, height=0)

        self.index_label = tk.Label(
            self, text=index, bg=bg, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"), cursor="hand2",
        )
        self.index_label.place(x=18, y=12)

        self.title_label = tk.Label(
            self, text=title, bg=bg, fg=TEXT_2,
            font=(FONT_UI, 10), cursor="hand2",
        )
        self.title_label.place(x=48, y=10)

        for widget in (self, self.index_label, self.title_label):
            widget.bind("<Button-1>", lambda _e: self.command())
            widget.bind("<Enter>", lambda _e: self._hover(True))
            widget.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, entering):
        if self.active:
            return
        self._tween("_hv", 1.0 if entering else 0.0, duration=200)

    def _paint(self):
        try:
            level = max(self._t, self._hv * 0.5)
            bg = mix(self.base_bg, LAYER_ALT, level)

            self.configure(bg=bg)
            self.index_label.configure(bg=bg, fg=mix(TEXT_3, ACCENT, self._t))
            self.title_label.configure(bg=bg, fg=mix(TEXT_2, TEXT, level))

            bar = int(round(22 * self._t))
            self.indicator.place(
                x=0, y=(40 - bar) // 2, width=3, height=max(0, bar),
            )
        except tk.TclError:
            return

    def _tween(self, attr, target, duration=200):
        start = getattr(self, attr)

        def frame(p):
            setattr(self, attr, start + (target - start) * p)
            self._paint()

        run_tween(self, duration, frame, attr=attr + "_anim")

    def destroy(self):
        cancel_after(self, "_t_anim", "_hv_anim")
        super().destroy()

    def set_active(self, active):
        self.active = active

        if active:
            self._tween("_t", 1.0, duration=300)
            self._tween("_hv", 0.0, duration=160)
        else:
            self._tween("_t", 0.0, duration=300)


# ============================================================
# Log panel
# ============================================================

class LogPanel(tk.Frame):
    def __init__(self, parent, title="OUTPUT", bg=None):
        page_bg = bg or parent.cget("bg")

        super().__init__(parent, bg=page_bg)

        head = tk.Frame(self, bg=page_bg)
        head.pack(fill="x", pady=(0, 8))

        tk.Label(
            head, text="▪", bg=page_bg, fg=ACCENT,
            font=(FONT_MONO, 7),
        ).pack(side="left", padx=(0, 7))

        tk.Label(
            head, text=title, bg=page_bg, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"),
        ).pack(side="left")

        Btn(
            head, "CLEAR", self.clear, style="subtle",
            height=24, padx=9, font=(FONT_MONO, 7, "bold"), bg=page_bg,
        ).pack(side="right")

        self.card = Card(
            self, radius=RADIUS, fill=LAYER, outline="",
            padx=4, pady=4, bg=page_bg,
        )
        self.card.pack(fill="both", expand=True)

        self.text = tk.Text(
            self.card.body,
            bg=LAYER, fg="#9c9c9c",
            insertbackground=ACCENT,
            selectbackground="#2c3a10", selectforeground=TEXT,
            relief="flat", bd=0, highlightthickness=0,
            font=(FONT_MONO, 9), wrap="word", height=6,
            padx=10, pady=8, spacing1=1, spacing3=2,
            state="disabled",
        )
        self.text.pack(fill="both", expand=True)

        self.text.tag_configure("t", foreground=TEXT_3)
        self.text.tag_configure("info", foreground="#9c9c9c")
        self.text.tag_configure("step", foreground=ACCENT)
        self.text.tag_configure("cmd", foreground="#6f6f6f")
        self.text.tag_configure("err", foreground=DANGER)

    def _alive(self):
        try:
            return bool(self.text.winfo_exists())
        except tk.TclError:
            return False

    def append(self, message):
        if not self._alive():
            return

        message = message.rstrip()

        if not message:
            self._insert("", "info")
            return

        if (
            message.startswith("错误")
            or message.startswith("Traceback")
            or message.startswith("FAIL")
        ):
            tag = "err"
        elif message.startswith("==="):
            tag = "step"
        elif message.startswith(">>>"):
            tag = "cmd"
        else:
            tag = "info"

        self._insert(message, tag)

    def _insert(self, message, tag):
        try:
            at_bottom = self.text.yview()[1] > 0.995
        except tk.TclError:
            return

        try:
            self.text.configure(state="normal")
            self.text.insert("end", time.strftime("%H:%M:%S") + "  ", "t")
            self.text.insert("end", message + "\n", tag)

            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > 3000:
                self.text.delete("1.0", "800.0")

            self.text.configure(state="disabled")

            if at_bottom:
                self.text.see("end")
        except tk.TclError:
            return

    def clear(self):
        if not self._alive():
            return
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
        except tk.TclError:
            return


# ============================================================
# Model setup dialog
# ============================================================

MANAGE_ENTRY = "__manage_translation_model__"

MODEL_DIALOG_COPY = {
    "translate": {
        "title": "翻译模型",
        "eyebrow": "TRANSLATION MODEL",
        "heading": "翻译模型未安装",
        "body": "「字幕翻译」需要本地翻译模型，约 4.3GB。安装包不含这个文件，"
                "点「自动下载」或手动放进下面的目录都可以。",
        "folder": "目标目录",
    },
    "subtitle": {
        "title": "字幕模型",
        "eyebrow": "SPEECH MODEL",
        "heading": "字幕模型未安装",
        "body": "语音识别模型（whisper，约 1.6GB）缺失，多半是安装不完整。"
                "重新运行一次安装包即可修复。",
        "folder": "模型应放在",
    },
}


class ModelDialog(tk.Toplevel):
    """模型安装提示。

    kind="translate"：列出四个档位，按显存推荐一个。只做建议——用户可以在
    四档里随便选，也可以自己准备 gguf 丢进目录。
    kind="subtitle"：字幕模型缺失属于安装不完整，只提示重新安装。
    """

    def __init__(self, master, missing, kind="translate"):
        super().__init__(master)

        # 先藏起来：Toplevel 一创建就会被映射到默认位置（左上角），
        # 摆好位置再 deiconify，否则会先闪一下再跳到中间。
        self.withdraw()

        copy = MODEL_DIALOG_COPY.get(kind, MODEL_DIALOG_COPY["translate"])

        self._kind = kind
        self._missing = missing
        self._worker = None
        self._cancelled = False
        self._queue = queue.Queue()
        self._dl_t0 = None
        self._dl_base = 0
        self._selected = None
        self._rows = {}
        self._installed = set(models_setup.installed_models())
        self._current = str(getattr(master, "model_var").get()) if hasattr(master, "model_var") else ""

        self.configure(bg=BG)
        self.title(copy["title"])
        self.resizable(False, False)
        self.transient(master)

        logo = getattr(master, "logo_image", None)
        if logo is not None:
            try:
                self.iconphoto(False, logo)
            except tk.TclError:
                pass

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(
            body, text=copy["eyebrow"], bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 7, "bold"), anchor="w",
        ).pack(fill="x")

        heading = copy["heading"]
        if kind == "translate" and self._installed:
            heading = "选择翻译模型"

        tk.Label(
            body, text=heading, bg=BG, fg=TEXT,
            font=(FONT_UI, 15, "bold"), anchor="w",
        ).pack(fill="x", pady=(6, 8))

        if kind == "translate":
            self._build_picker(body)
            geometry = "600x516"
        else:
            self._build_missing(body, copy)
            geometry = "580x372"

        self.progress = Bar(body, height=6, bg=BG)
        self.progress.pack(fill="x", pady=(12, 0))
        self.progress.pack_forget()

        self.hint = tk.Label(
            body, text="", bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w", justify="left", wraplength=540,
        )
        self.hint.pack(fill="x", pady=(8, 0))

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(14, 0))

        Btn(actions, "稍后", self._close, style="subtle",
            height=32, padx=16, bg=BG).pack(side="right")

        Btn(actions, "重新检测", self._recheck, style="standard",
            height=32, padx=16, bg=BG).pack(side="right", padx=(0, 8))

        Btn(actions, "打开文件夹", self._open_folder, style="standard",
            height=32, padx=16, bg=BG).pack(side="right", padx=(0, 8))

        self.download_btn = None
        if kind == "translate":
            self.download_btn = Btn(
                actions, "下载", self._primary_action, style="accent",
                height=32, padx=18, bg=BG,
            )
            self.download_btn.pack(side="left")
            self._sync_download_btn()

        self.update_idletasks()
        self._place(master, geometry)
        self.deiconify()

        self.bind("<Escape>", lambda _e: self._close())
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(20, lambda: darken_titlebar(self))

    # --------------------------------------------------------
    # 布局
    # --------------------------------------------------------

    def _build_picker(self, body):
        chosen, device = models_setup.recommend()
        self._selected = chosen

        if device:
            hardware = (
                f"检测到 {device['name']} · "
                f"{device['total_mib'] / 1024:.0f}GB 显存"
            )
        else:
            hardware = "没有检测到可用的 GPU，将使用 CPU"

        tk.Label(
            body, text=f"{hardware}，推荐「{chosen['label']}」。",
            bg=BG, fg=TEXT_2, font=(FONT_UI, 9),
            anchor="w", justify="left", wraplength=540,
        ).pack(fill="x")

        rows = tk.Frame(body, bg=BG)
        rows.pack(fill="x", pady=(12, 0))

        for item in models_setup.REQUIRED:
            self._build_row(rows, item, item is chosen)

        tk.Label(
            body,
            text=f"也可以自己准备 gguf 模型，放进下面这个目录：\n"
                 f"{models_setup.target_dir()}",
            bg=BG, fg=TEXT_3, font=(FONT_MONO, 8),
            anchor="w", justify="left", wraplength=540,
        ).pack(fill="x", pady=(12, 0))

    def _build_row(self, parent, item, recommended):
        frame = tk.Frame(parent, bg=LAYER, cursor="hand2")
        frame.pack(fill="x", pady=3)

        mark = tk.Label(
            frame, text="○", bg=LAYER, fg=TEXT_3, font=(FONT_MONO, 9),
        )
        mark.pack(side="left", padx=(12, 10), pady=10)

        middle = tk.Frame(frame, bg=LAYER)
        middle.pack(side="left", fill="x", expand=True, pady=8)

        title = tk.Label(
            middle, text=item["label"], bg=LAYER, fg=TEXT,
            font=(FONT_UI, 10, "bold"), anchor="w",
        )
        title.pack(fill="x")

        note = tk.Label(
            middle, text=f"{item['tier']} · {item['note']}", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7), anchor="w",
        )
        note.pack(fill="x", pady=(3, 0))

        size = item.get("size") or 0
        tk.Label(
            frame, text=human_size_bytes(size) if size else "—",
            bg=LAYER, fg=TEXT_2, font=(FONT_MONO, 8),
        ).pack(side="right", padx=(0, 12))

        filename = item["filename"]
        if filename in self._installed:
            using = filename == self._current
            tk.Label(
                frame, text="使用中" if using else "已安装",
                bg=LAYER, fg=ACCENT if using else TEXT_2,
                font=(FONT_MONO, 7, "bold"),
            ).pack(side="right", padx=(0, 10))
        elif recommended:
            tk.Label(
                frame, text="推荐", bg=LAYER, fg=ACCENT,
                font=(FONT_MONO, 7, "bold"),
            ).pack(side="right", padx=(0, 10))

        self._rows[item["key"]] = {
            "frame": frame, "mark": mark, "title": title, "note": note,
            "size": frame.winfo_children()[-1] if not recommended else None,
        }

        for widget in (frame, mark, middle, title, note):
            widget.bind("<Button-1>", lambda _e, key=item["key"]: self._select(key))

        self._paint_row(item["key"])

    def _paint_row(self, key):
        row = self._rows.get(key)
        if not row:
            return

        selected = self._selected is not None and self._selected["key"] == key
        background = LAYER_ALT if selected else LAYER

        try:
            for name in ("frame", "mark", "title", "note"):
                row[name].configure(bg=background)
            row["mark"].configure(
                text="●" if selected else "○",
                fg=ACCENT if selected else TEXT_3,
            )
            row["title"].configure(fg=TEXT if selected else TEXT_2)
        except tk.TclError:
            return

    def _build_missing(self, body, copy):
        tk.Label(
            body, text=copy["body"], bg=BG, fg=TEXT_2,
            font=(FONT_UI, 9), anchor="w", justify="left", wraplength=500,
        ).pack(fill="x")

        card = Card(body, radius=RADIUS, fill=LAYER, outline="",
                    padx=4, pady=4, bg=BG)
        card.pack(fill="x", pady=(14, 0))

        inner = tk.Frame(card.body, bg=LAYER)
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(
            inner, text=copy["folder"], bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7, "bold"), anchor="w",
        ).pack(fill="x")

        tk.Label(
            inner, text=str(models_setup.bundled_dir()),
            bg=LAYER, fg=TEXT, font=(FONT_MONO, 8),
            anchor="w", justify="left", wraplength=460,
        ).pack(fill="x", pady=(4, 0))

        names = "、".join(item["filename"] for item in self._missing)
        tk.Label(
            inner, text=names, bg=LAYER, fg=TEXT_2,
            font=(FONT_MONO, 8), anchor="w", justify="left", wraplength=460,
        ).pack(fill="x", pady=(8, 0))

    # --------------------------------------------------------
    # 交互
    # --------------------------------------------------------

    def _select(self, key):
        if self._worker is not None:
            return

        item = models_setup.by_key(key)
        if item is None:
            return

        self._selected = item
        for other in self._rows:
            self._paint_row(other)
        self._sync_download_btn()

    def _sync_download_btn(self):
        if self.download_btn is None or self._selected is None:
            return

        item = self._selected
        filename = item["filename"]
        installed = filename in self._installed

        if installed and filename == self._current:
            text, state, hint = "使用中", "disabled", ""
        elif installed:
            text, state, hint = "使用这个模型", "normal", ""
        else:
            size = item.get("size") or 0
            text = f"下载 {item['label']}"
            if size:
                text += f"（{human_size_bytes(size)}）"

            has_url = bool(item.get("url"))
            state = "normal" if has_url else "disabled"
            hint = "" if has_url else "这一档暂未配置下载地址，请手动把文件放进上面那个目录。"

        self.download_btn.set_text(text)
        self.download_btn.set_state(state)

        if not self._cancelled:
            self.hint.configure(text=hint, fg=TEXT_3)

    def _primary_action(self):
        item = self._selected
        if item is None:
            return

        if item["filename"] in self._installed:
            self._use_model(item["filename"])
        else:
            self._start_download()

    def _use_model(self, filename):
        try:
            self.master._select_model(filename)
        except Exception:
            pass
        self._close()

    def _open_folder(self):
        if self._kind == "subtitle":
            models_setup.open_bundled_folder()
        else:
            models_setup.open_folder()

    def _place(self, master, geometry):
        """按给定的尺寸在主窗口上居中，一次到位。"""
        try:
            width, height = (int(v) for v in geometry.split("x"))
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        except (ValueError, tk.TclError):
            return

        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _close(self):
        self._cancelled = True

        if self._kind == "translate":
            try:
                self.master._dismiss_translate_prompt()
            except Exception:
                pass

        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _recheck(self):
        if self._kind == "subtitle":
            still = models_setup.missing_bundled()
        else:
            still = models_setup.missing()

        if not still:
            self._close()
            return

        self.hint.configure(text="仍然没有找到模型文件。", fg=DANGER)

    def _start_download(self):
        if self._worker is not None:
            return

        item = self._selected
        if item is None or not item.get("url"):
            self.hint.configure(text="这一档还没有配置下载地址，请手动放置文件。", fg=TEXT_3)
            return

        self.progress.pack(fill="x", pady=(12, 0))
        self.hint.configure(text="正在下载…", fg=TEXT_2)
        self.download_btn.set_state("disabled")

        def on_progress(done, total):
            self._queue.put(("progress", done, total))

        def work():
            try:
                models_setup.download(
                    item, on_progress=on_progress,
                    cancelled=lambda: self._cancelled,
                )
                self._queue.put(("done", 0, 0))
            except InterruptedError:
                self._queue.put(("cancelled", 0, 0))
            except Exception as exc:
                self._queue.put(("error", str(exc), 0))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self._poll()

    def _poll(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            while True:
                kind, a, b = self._queue.get_nowait()

                if kind == "progress":
                    if b:
                        now = time.time()
                        if self._dl_t0 is None:
                            self._dl_t0 = now
                            self._dl_base = a

                        elapsed = max(0.001, now - self._dl_t0)
                        speed = max(0.0, (a - self._dl_base) / elapsed)
                        eta = (b - a) / speed if speed > 0 else 0

                        self.progress.set(a * 100.0 / b)
                        self.hint.configure(
                            text=(
                                f"{human_size_bytes(a)} / {human_size_bytes(b)}"
                                f"   ·   {human_size_bytes(speed)}/s"
                                f"   ·   剩余 {format_eta(eta)}"
                            ),
                            fg=TEXT_2,
                        )
                elif kind == "done":
                    self.hint.configure(text="下载完成，已切换到这个模型。", fg=ACCENT)
                    self.progress.set(100)
                    self._worker = None
                    if self._selected is not None:
                        self._installed.add(self._selected["filename"])
                        self._use_model(self._selected["filename"])
                    return
                elif kind == "cancelled":
                    self.hint.configure(text="已取消。", fg=TEXT_3)
                    self._worker = None
                    self._sync_download_btn()
                    return
                elif kind == "error":
                    self.hint.configure(text=f"下载失败：{a}", fg=DANGER)
                    self._worker = None
                    self._sync_download_btn()
                    return
        except queue.Empty:
            pass

        self.after(120, self._poll)


# ============================================================
# App
# ============================================================

class SubtitleTool(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.minsize(1120, 810)
        self._center(1200, 840)
        self._set_icon()

        self.selected_video = None
        self.selected_mkv = None
        self.current_page = None

        self.busy = False
        self.job_name = ""

        self.progress = None
        self.progress_text = None
        self.progress_value = None
        self.log_panel = None
        self.start_pill = None
        self.stages = []
        self._active_log = None

        self.video_name = None
        self.video_meta = None
        self.mkv_name = None
        self.mkv_meta = None
        self.segmented = None
        self.engine_note = None
        self.model_box = None
        self.model_label = None
        self.model_size = None
        self._model_popup = None

        self._queue = queue.Queue()
        self._pump_id = None
        self._page_anim = None
        self.page_host = None
        self._nav_items = {}

        self._timer_boosted = boost_timer_resolution()

        self._build_shell()
        self._pump()

        self.bind("<Control-Return>", lambda _e: self._start_current())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._goto("home")
        self.after(20, self._apply_dark_titlebar)
        self.after(300, self._apply_dark_titlebar)

        # 安装包不带翻译模型，首次启动提示一次
        self.after(900, self._check_models)

    def _check_models(self):
        if self.busy or not self.winfo_exists():
            return

        try:
            # 字幕模型随「字幕模型包」安装，缺了连语音识别都做不了，优先提示
            missing_sub = models_setup.missing_bundled()
            if missing_sub:
                ModelDialog(self, missing_sub, kind="subtitle")
                return

            missing = models_setup.missing()
            dismissed = (
                self._read_config()
                .get("translation", {})
                .get("setup_dismissed", False)
            )
            # 用户自己放了别的量化版本、或者点过「稍后」，都别再打扰
            if missing and not models_setup.has_any_model() and not dismissed:
                ModelDialog(self, missing, kind="translate")
        except tk.TclError:
            pass

    # --------------------------------------------------------
    # Window
    # --------------------------------------------------------

    def _center(self, width, height):
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _set_icon(self):
        self.logo_image = None

        if not LOGO_PATH.exists():
            return

        try:
            image = tk.PhotoImage(file=str(LOGO_PATH))
            factor = max(1, round(image.width() / 22))
            if factor > 1:
                image = image.subsample(factor, factor)
            self.logo_image = image
            # default=True：后面弹出的对话框（模型提示等）自动继承这个图标，
            # 否则子窗口左上角会是 Tk 默认的羽毛
            self.iconphoto(True, image)
        except Exception:
            self.logo_image = None

    def _apply_dark_titlebar(self):
        darken_titlebar(self)

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno("任务进行中", "当前任务仍在运行，确定要退出吗？"):
                return

        if self._pump_id is not None:
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:
                pass

        cancel_after(self, "_page_anim")

        if getattr(self, "_timer_boosted", False):
            release_timer_resolution()

        self.destroy()

    # --------------------------------------------------------
    # Thread -> UI queue
    # --------------------------------------------------------

    def _ui(self, callback):
        self._queue.put(callback)

    def _pump(self):
        try:
            while True:
                callback = self._queue.get_nowait()
                try:
                    callback()
                except tk.TclError:
                    pass
                except Exception:
                    pass
        except queue.Empty:
            pass

        try:
            self._pump_id = self.after(60, self._pump)
        except tk.TclError:
            self._pump_id = None

    # --------------------------------------------------------
    # Shell
    # --------------------------------------------------------

    def _build_shell(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=SIDEBAR_W)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR, height=62)
        brand.pack(fill="x")
        brand.pack_propagate(False)

        if self.logo_image is not None:
            tk.Label(brand, image=self.logo_image, bg=SIDEBAR).place(x=20, y=22)
        else:
            tk.Label(
                brand, text="[ST]", bg=SIDEBAR, fg=ACCENT,
                font=(FONT_MONO, 10, "bold"),
            ).place(x=20, y=22)

        tk.Label(
            brand, text="Subtitle Tool", bg=SIDEBAR, fg=TEXT,
            font=(FONT_UI, 10, "bold"),
        ).place(x=52, y=14)

        tk.Label(
            brand, text="LOCAL PIPELINE", bg=SIDEBAR, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).place(x=52, y=34)

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x", pady=(10, 0))

        for index, key, title in (
            ("01", "home", "工作台"),
            ("02", "japanese", "日语字幕"),
            ("03", "translate", "字幕翻译"),
        ):
            item = NavItem(nav, index, title, lambda k=key: self._goto(k), bg=SIDEBAR)
            item.pack(fill="x", padx=8, pady=1)
            self._nav_items[key] = item

        footer = tk.Frame(sidebar, bg=SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=20, pady=16)

        self.status = StatusDot(footer, bg=SIDEBAR)
        self.status.pack(anchor="w")

        tk.Label(
            footer, text=f"v{APP_VERSION}  ·  OFFLINE", bg=SIDEBAR, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).pack(anchor="w", pady=(8, 0))

        tk.Frame(self, bg=STROKE_SOFT, width=1).pack(side="left", fill="y")

        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

    # --------------------------------------------------------
    # Navigation
    # --------------------------------------------------------

    def _goto(self, page, animate=True):
        self.current_page = page

        for key, item in self._nav_items.items():
            item.set_active(key == page)

        cancel_after(self, "_page_anim")

        for widget in self.main.winfo_children():
            widget.destroy()

        self.progress = None
        self.progress_text = None
        self.progress_value = None
        self.log_panel = None
        self.start_pill = None
        self.stages = []

        self.page_host = tk.Frame(self.main, bg=BG)
        self.page_host.place(x=0, y=0, relwidth=1, relheight=1)

        builder = {
            "home": self._page_home,
            "japanese": self._page_japanese,
            "translate": self._page_translate,
        }.get(page)

        if builder is not None:
            builder()

        if animate:
            self._slide_page()

    def _slide_page(self):
        """页面进场：从下方 88px 处上滑归位。

        对应 WinUI 的 EntranceNavigationTransitionInfo（NavigationView 默认
        的页面切换）：上滑 + 减速。88px 让位移足够明显；底部动作按钮在起始帧
        完全落在窗口下沿之外，所以是"整块升上来"，不会被切掉一半。
        300ms 配 ease_soft，单帧最大跳变约 15px，够快但不散。
        """
        offset = 88
        self.page_host.place_configure(x=0, y=offset)

        def frame(p):
            self.page_host.place_configure(
                x=0, y=round(offset * (1.0 - p)),
            )

        run_tween(self, 300, frame, attr="_page_anim", curve=ease_soft)

    # 兼容旧调用
    def show_home(self):
        self._goto("home")

    def show_japanese(self):
        self._goto("japanese")

    def show_translate(self):
        self._goto("translate")

    # --------------------------------------------------------
    # Small helpers
    # --------------------------------------------------------

    def _alive(self, widget):
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except (tk.TclError, AttributeError):
            return False

    def _page_body(self):
        body = tk.Frame(self.page_host, bg=BG)
        body.pack(fill="both", expand=True, padx=PAGE_PAD, pady=(26, 22))
        return body

    def _page_header(self, parent, eyebrow, title, desc):
        tk.Label(
            parent, text=eyebrow, bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"), anchor="w",
        ).pack(fill="x")

        tk.Label(
            parent, text=title, bg=BG, fg=TEXT,
            font=(FONT_UI, 20, "bold"), anchor="w",
        ).pack(fill="x", pady=(9, 6))

        tk.Label(
            parent, text=desc, bg=BG, fg=TEXT_2,
            font=(FONT_UI, 9), anchor="w",
        ).pack(fill="x")

    def _group(self, parent, title, note=None):
        """分组标题。调用方负责 .pack()，以便控制顺序。"""
        row = tk.Frame(parent, bg=BG)

        tk.Label(
            row, text="▪", bg=BG, fg=ACCENT, font=(FONT_MONO, 7),
        ).pack(side="left", padx=(0, 7))

        tk.Label(
            row, text=title, bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"),
        ).pack(side="left")

        if note:
            tk.Label(
                row, text=note, bg=BG, fg=TEXT_3,
                font=(FONT_MONO, 7),
            ).pack(side="right")

        return row

    def _setting_row(self, parent, title, desc=None, control=None, divider=True):
        row = tk.Frame(parent, bg=LAYER)
        row.pack(fill="x", padx=12, pady=(12, 12))

        left = tk.Frame(row, bg=LAYER)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left, text=title, bg=LAYER, fg=TEXT,
            font=(FONT_UI, 10), anchor="w",
        ).pack(fill="x")

        if desc:
            tk.Label(
                left, text=desc, bg=LAYER, fg=TEXT_3,
                font=(FONT_MONO, 8), anchor="w",
            ).pack(fill="x", pady=(4, 0))

        if control is not None:
            control.pack(side="right", padx=(14, 0))

        if divider:
            tk.Frame(parent, bg=STROKE_SOFT, height=1).pack(fill="x", padx=12)

        return row

    def _tile(self, parent, text, size=38, fg=ACCENT, bg=LAYER_ALT):
        canvas = tk.Canvas(
            parent, width=size, height=size, bg=parent.cget("bg"),
            highlightthickness=0, bd=0,
        )
        round_rect(canvas, 0, 0, size, size, 8, fill=bg, outline="")
        canvas.create_text(
            size / 2, size / 2 + 1, text=text, fill=fg,
            font=(FONT_MONO, 11, "bold"),
        )
        return canvas

    def _stat(self, parent, label, value, ok=True):
        box = tk.Frame(parent, bg=LAYER)

        tk.Label(
            box, text=label, bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7, "bold"),
        ).pack(anchor="w")

        tk.Label(
            box, text=value, bg=LAYER,
            fg=TEXT if ok else DANGER,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor="w", pady=(5, 0))

        return box

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    def _default_config(self):
        return {
            "translation": {
                "engine": "local",
                "model": "Hy-MT2-7B-Q4_K_M.gguf",
                "api_url": "http://127.0.0.1:8080/v1/chat/completions",
                "api_key": "",
                "api_model": "",
                "setup_dismissed": False,
            }
        }

    def _read_config(self):
        if not CONFIG_FILE.exists():
            config = self._default_config()
            self._write_config(config)
            return config

        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return self._default_config()

    def _dismiss_translate_prompt(self):
        """用户点过「稍后」就记下来，下次启动不再弹。"""
        config = self._read_config()
        config.setdefault("translation", {})["setup_dismissed"] = True
        self._write_config(config)

    def _write_config(self, config):
        try:
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _local_models(self):
        if not MODEL_DIR.exists():
            return []

        return sorted(
            (path for path in MODEL_DIR.glob("*.gguf") if path.is_file()),
            key=lambda path: path.name.lower(),
        )

    # --------------------------------------------------------
    # Page: home
    # --------------------------------------------------------

    def _page_home(self):
        body = self._page_body()

        self._page_header(
            body,
            "WORKBENCH  /  01",
            "字幕工作台",
            "本地离线的日语字幕流水线：识别、断句、翻译、封装",
        )

        config = self._read_config().get("translation", {})
        models = self._local_models()

        cards = tk.Frame(body, bg=BG)
        cards.pack(fill="x", pady=(26, 0))

        cards.grid_columnconfigure(0, weight=1, uniform="home")
        cards.grid_columnconfigure(1, minsize=14)
        cards.grid_columnconfigure(2, weight=1, uniform="home")
        cards.grid_rowconfigure(0, minsize=300)

        self._home_card(
            cards, 0, "JA", "日语字幕", "VIDEO → JAPANESE SUBTITLE",
            "语音识别 → VAD 分段 → SRT 断句 → 封装 MKV",
            ["WHISPER", "VAD", "SRT", "MKV"],
            "输出  *.ja.mkv",
            lambda: self._goto("japanese"),
        )

        self._home_card(
            cards, 2, "ZH", "字幕翻译", "JAPANESE → CHINESE",
            "将已有日语字幕翻译为简体中文，输出双语 MKV",
            ["LOCAL 7B", "API", "BILINGUAL"],
            "输出  *.ja.zh.mkv",
            lambda: self._goto("translate"),
        )

        footer = tk.Frame(body, bg=BG)
        footer.pack(side="bottom", fill="x")

        tk.Frame(footer, bg=STROKE_SOFT, height=1).pack(fill="x", pady=(0, 12))

        row = tk.Frame(footer, bg=BG)
        row.pack(fill="x")

        tk.Label(
            row, text="© 2026 REN   ·   OPENAI × GPT-5.6 LUNA",
            bg=BG, fg=TEXT_3, font=(FONT_MONO, 7),
        ).pack(side="left")

        tk.Label(
            row,
            text=f"{config.get('engine', 'local').upper()}  ·  {config.get('model', '-')}",
            bg=BG, fg=TEXT_3, font=(FONT_MONO, 7),
        ).pack(side="right")

        self._group(body, "WORKFLOW", "3 STEPS").pack(fill="x", pady=(20, 8))

        workflow = Card(body, radius=RADIUS, fill=LAYER, outline="",
                        padx=4, pady=4)
        workflow.pack(fill="x")

        wf = tk.Frame(workflow.body, bg=LAYER)
        wf.pack(fill="x", padx=12, pady=20)

        steps = [
            ("01", "语音识别", "VAD 分段 + WHISPER"),
            ("02", "断句与时间轴", "SUDACHI 分词"),
            ("03", "封装", "FFMPEG → MKV"),
        ]

        for i, (number, title, sub) in enumerate(steps):
            column = i * 2
            wf.grid_columnconfigure(column, weight=1, uniform="wf")

            cell = tk.Frame(wf, bg=LAYER)
            cell.grid(row=0, column=column, sticky="w")

            tk.Label(
                cell, text=number, bg=LAYER, fg=TEXT_3,
                font=(FONT_MONO, 7, "bold"), anchor="w",
            ).pack(fill="x")

            tk.Label(
                cell, text=title, bg=LAYER, fg=TEXT,
                font=(FONT_UI, 9), anchor="w",
            ).pack(fill="x", pady=(5, 3))

            tk.Label(
                cell, text=sub, bg=LAYER, fg=TEXT_3,
                font=(FONT_MONO, 7), anchor="w",
            ).pack(fill="x")

            if i < len(steps) - 1:
                wf.grid_columnconfigure(column + 1, minsize=25)
                tk.Frame(wf, bg=STROKE_SOFT, width=1).grid(
                    row=0, column=column + 1, sticky="ns", pady=2,
                )

        self._group(body, "ENVIRONMENT", "LOCAL RUNTIME").pack(fill="x", pady=(20, 8))

        card = Card(body, radius=RADIUS, fill=LAYER, outline="",
                    padx=4, pady=4, bg=BG)
        card.pack(fill="x")

        env = tk.Frame(card.body, bg=LAYER)
        env.pack(fill="x", padx=12, pady=18)

        runtime = APP_DATA / "runtime"
        items = [
            ("FFMPEG", runtime / "ffmpeg" / "ffmpeg.exe", None),
            ("WHISPER", runtime / "whisper" / "whisper-cli.exe", None),
            ("LLAMA", runtime / "llama" / "llama-server.exe", None),
            ("MODELS", MODEL_DIR, len(models)),
        ]

        for column, (name, path, count) in enumerate(items):
            env.grid_columnconfigure(column, weight=1, uniform="env")
            ok = path.exists() and (count is None or count > 0)
            detail = (
                f"{count} FILES" if count is not None else ("READY" if ok else "MISSING")
            )
            if not ok:
                detail = "MISSING"

            box = self._stat(env, name, detail, ok)
            box.grid(row=0, column=column, sticky="w")

    def _home_card(self, parent, column, glyph, title, subtitle,
                   description, chips, footnote, command):
        card = Card(parent, radius=RADIUS, fill=LAYER, outline="",
                    padx=4, pady=4)
        card.grid(row=0, column=column, sticky="nsew")

        body = tk.Frame(card.body, bg=LAYER)
        body.pack(fill="both", expand=True, padx=14, pady=14)

        self._tile(body, glyph).pack(anchor="w")

        tk.Label(
            body, text=title, bg=LAYER, fg=TEXT,
            font=(FONT_UI, 15, "bold"), anchor="w",
        ).pack(fill="x", pady=(14, 4))

        tk.Label(
            body, text=subtitle, bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        ).pack(fill="x")

        tk.Label(
            body, text=description, bg=LAYER, fg=TEXT_2,
            font=(FONT_UI, 9), anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", pady=(12, 0))

        chip_row = tk.Frame(body, bg=LAYER)
        chip_row.pack(fill="x", pady=(14, 0))

        for text in chips:
            Chip(chip_row, text, bg=LAYER).pack(side="left", padx=(0, 6))

        tk.Frame(body, bg=LAYER).pack(fill="both", expand=True)

        tk.Frame(body, bg=STROKE_SOFT, height=1).pack(fill="x", pady=(14, 10))

        foot = tk.Frame(body, bg=LAYER)
        foot.pack(fill="x")

        tk.Label(
            foot, text=footnote, bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).pack(side="left")

        button = Btn(foot, "进入", command, style="subtle",
                     height=28, padx=12, bg=LAYER)
        button.pack(side="right")

        def enter():
            card.set_outline("#3a3a3a")
            button.set_style("standard")

        def leave():
            card.set_outline(STROKE)
            button.set_style("subtle")

        self._hover_bind(card, enter, leave)

    def _hover_bind(self, widget, on_enter, on_leave):
        def enter(_event):
            on_enter()

        def leave(event):
            node = self.winfo_containing(event.x_root, event.y_root)
            while node is not None:
                if node is widget:
                    return
                node = getattr(node, "master", None)
            on_leave()

        def walk(current):
            current.bind("<Enter>", enter, add="+")
            current.bind("<Leave>", leave, add="+")
            for child in current.winfo_children():
                walk(child)

        walk(widget)

    # --------------------------------------------------------
    # Page: japanese
    # --------------------------------------------------------

    def _page_japanese(self):
        body = self._page_body()

        self._page_header(
            body,
            "02  /  JAPANESE SUBTITLE",
            "日语字幕生成",
            "视频 → 语音识别 → 断句 → 日语字幕 MKV",
        )

        self._group(body, "SOURCE").pack(fill="x", pady=(22, 8))

        source = Card(body, radius=RADIUS, fill=LAYER, outline="",
                      padx=4, pady=4)
        source.pack(fill="x")

        row = tk.Frame(source.body, bg=LAYER)
        row.pack(fill="x", padx=12, pady=12)

        info = tk.Frame(row, bg=LAYER)
        info.pack(side="left", fill="x", expand=True)

        self.video_name = tk.Label(
            info, text="未选择视频文件", bg=LAYER, fg=TEXT_2,
            font=(FONT_UI, 10), anchor="w",
        )
        self.video_name.pack(fill="x")

        self.video_meta = tk.Label(
            info, text="MP4 · MKV · MOV · AVI · WEBM", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        )
        self.video_meta.pack(fill="x", pady=(4, 0))

        Btn(row, "浏览", self.select_video, style="standard",
            height=30, padx=16, bg=LAYER).pack(side="right", padx=(14, 0))

        self._group(body, "PIPELINE", "3 STAGES").pack(fill="x", pady=(18, 8))

        pipeline = Card(body, radius=RADIUS, fill=LAYER, outline="",
                        padx=4, pady=4)
        pipeline.pack(fill="x")

        self.stages = []

        for number, (name, cn_name) in enumerate(
            [
                ("TRANSCRIPTION", "语音识别"),
                ("SRT BUILD", "断句与时间轴"),
                ("JAPANESE MKV", "封装日语字幕"),
            ],
            start=1,
        ):
            self.stages.append(
                self._stage_row(pipeline.body, number, name, cn_name,
                                divider=number < 3)
            )

        self._group(body, "PROGRESS").pack(fill="x", pady=(18, 8))

        progress_card = Card(body, radius=RADIUS, fill=LAYER, outline="",
                             padx=4, pady=4)
        progress_card.pack(fill="x")

        progress_inner = tk.Frame(progress_card.body, bg=LAYER)
        progress_inner.pack(fill="x", padx=14, pady=12)

        self.progress = Bar(progress_inner, height=6, bg=LAYER)
        self.progress.pack(fill="x")

        prow = tk.Frame(progress_inner, bg=LAYER)
        prow.pack(fill="x", pady=(9, 0))

        self.progress_text = tk.Label(
            prow, text="等待开始", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        )
        self.progress_text.pack(side="left")

        self.progress_value = tk.Label(
            prow, text="0%", bg=LAYER, fg=ACCENT,
            font=(FONT_MONO, 8, "bold"),
        )
        self.progress_value.pack(side="right")

        actions = tk.Frame(body, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(14, 0))

        tk.Label(
            actions, text="CTRL + ENTER 快速开始", bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).pack(side="left")

        self.start_pill = Btn(
            actions, "开始处理", self.start_japanese,
            style="accent", height=34, padx=22, bg=BG,
        )
        self.start_pill.pack(side="right")

        self.log_panel = LogPanel(body, "OUTPUT", bg=BG)
        self.log_panel.pack(fill="both", expand=True, pady=(16, 0))
        self.log_panel.append("等待选择视频…")

    def _stage_row(self, parent, number, name, cn_name, divider=True):
        row = tk.Frame(parent, bg=LAYER)
        row.pack(fill="x", padx=12, pady=11)

        tk.Label(
            row, text=f"{number:02d}", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8, "bold"),
        ).pack(side="left", padx=(0, 14))

        left = tk.Frame(row, bg=LAYER)
        left.pack(side="left", fill="x", expand=True)

        name_label = tk.Label(
            left, text=cn_name, bg=LAYER, fg=TEXT,
            font=(FONT_UI, 10), anchor="w",
        )
        name_label.pack(fill="x")

        tk.Label(
            left, text=name, bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7), anchor="w",
        ).pack(fill="x", pady=(3, 0))

        chip = Chip(row, "WAIT", bg=LAYER)
        chip.pack(side="right", padx=(14, 0))

        if divider:
            tk.Frame(parent, bg=STROKE_SOFT, height=1).pack(fill="x", padx=12)

        return (name_label, chip)

    def _set_stage(self, index, state):
        if index < 0 or index >= len(self.stages):
            return

        name_label, chip = self.stages[index]

        try:
            if state == "wait":
                name_label.configure(fg=TEXT)
                chip.set("WAIT", TEXT_3, None, STROKE)
            elif state == "run":
                name_label.configure(fg=TEXT)
                chip.set("RUN", ACCENT, None, ACCENT_DIM)
            elif state == "done":
                name_label.configure(fg=TEXT)
                chip.set("DONE", "#0a0a0a", ACCENT, ACCENT)
            elif state == "error":
                name_label.configure(fg=DANGER)
                chip.set("FAIL", DANGER, None, "#5a2a2a")
        except tk.TclError:
            return

    def _reset_stages(self):
        for index in range(len(self.stages)):
            self._set_stage(index, "wait")

    def _fail_running_stage(self):
        for index, (_label, chip) in enumerate(self.stages):
            try:
                if chip._text == "RUN":
                    self._set_stage(index, "error")
                    return
            except Exception:
                return

    # --------------------------------------------------------
    # Page: translate
    # --------------------------------------------------------

    def _page_translate(self):
        body = self._page_body()

        self._page_header(
            body,
            "03  /  SUBTITLE TRANSLATION",
            "字幕翻译",
            "日语字幕 → 简体中文，输出双语 MKV",
        )

        self._group(body, "INPUT").pack(fill="x", pady=(22, 8))

        source = Card(body, radius=RADIUS, fill=LAYER, outline="",
                      padx=4, pady=4)
        source.pack(fill="x")

        row = tk.Frame(source.body, bg=LAYER)
        row.pack(fill="x", padx=12, pady=12)

        info = tk.Frame(row, bg=LAYER)
        info.pack(side="left", fill="x", expand=True)

        self.mkv_name = tk.Label(
            info, text="未选择日语字幕 MKV", bg=LAYER, fg=TEXT_2,
            font=(FONT_UI, 10), anchor="w",
        )
        self.mkv_name.pack(fill="x")

        self.mkv_meta = tk.Label(
            info, text="需包含日语字幕轨", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        )
        self.mkv_meta.pack(fill="x", pady=(4, 0))

        Btn(row, "浏览", self.select_mkv, style="standard",
            height=30, padx=16, bg=LAYER).pack(side="right", padx=(14, 0))

        direction = tk.Frame(source.body, bg=LAYER)
        direction.pack(fill="x", padx=12, pady=(0, 12))

        Chip(direction, "JAPANESE", bg=LAYER).pack(side="left")
        tk.Label(
            direction, text="→", bg=LAYER, fg=ACCENT,
            font=(FONT_MONO, 10, "bold"),
        ).pack(side="left", padx=9)
        Chip(direction, "简体中文", bg=LAYER).pack(side="left")

        tk.Label(
            direction, text="FIXED DIRECTION", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).pack(side="right")

        self._group(body, "ENGINE").pack(fill="x", pady=(18, 8))

        engine_card = Card(body, radius=RADIUS, fill=LAYER, outline="",
                           padx=4, pady=4)
        engine_card.pack(fill="x")

        config = self._read_config().setdefault("translation", {})

        self.engine_var = tk.StringVar(value=config.get("engine", "local"))
        self.model_var = tk.StringVar(value=config.get("model", "Hy-MT2-7B-Q4_K_M.gguf"))
        self.api_url_var = tk.StringVar(
            value=config.get("api_url", "http://127.0.0.1:8080/v1/chat/completions")
        )
        self.api_key_var = tk.StringVar(value=config.get("api_key", ""))
        self.api_model_var = tk.StringVar(value=config.get("api_model", ""))

        engine_head = tk.Frame(engine_card.body, bg=LAYER)
        engine_head.pack(fill="x", padx=12, pady=(12, 0))

        self.segmented = Segmented(
            engine_head,
            [("local", "LOCAL"), ("api", "API")],
            self.engine_var.get(),
            self._engine_changed,
            height=30,
            width=200,
            bg=LAYER,
        )
        self.segmented.pack(side="left")

        self.engine_note = tk.Label(
            engine_head, text="", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7), anchor="e",
        )
        self.engine_note.pack(side="right")

        self.local_frame = tk.Frame(engine_card.body, bg=LAYER)
        self.api_frame = tk.Frame(engine_card.body, bg=LAYER)

        self._build_local_engine(self.local_frame)
        self._build_api_engine(self.api_frame)

        self._engine_changed(self.engine_var.get(), save=False)

        self._group(body, "PROGRESS").pack(fill="x", pady=(18, 8))

        progress_card = Card(body, radius=RADIUS, fill=LAYER, outline="",
                             padx=4, pady=4)
        progress_card.pack(fill="x")

        progress_inner = tk.Frame(progress_card.body, bg=LAYER)
        progress_inner.pack(fill="x", padx=14, pady=12)

        self.progress = Bar(progress_inner, height=6, bg=LAYER)
        self.progress.pack(fill="x")

        prow = tk.Frame(progress_inner, bg=LAYER)
        prow.pack(fill="x", pady=(9, 0))

        self.progress_text = tk.Label(
            prow, text="等待开始", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        )
        self.progress_text.pack(side="left")

        self.progress_value = tk.Label(
            prow, text="0%", bg=LAYER, fg=ACCENT,
            font=(FONT_MONO, 8, "bold"),
        )
        self.progress_value.pack(side="right")

        actions = tk.Frame(body, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(14, 0))

        tk.Label(
            actions, text="CTRL + ENTER 快速开始", bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 7),
        ).pack(side="left")

        self.start_pill = Btn(
            actions, "开始翻译", self.start_translation,
            style="accent", height=34, padx=22, bg=BG,
        )
        self.start_pill.pack(side="right")

        self.log_panel = LogPanel(body, "OUTPUT", bg=BG)
        self.log_panel.pack(fill="both", expand=True, pady=(16, 0))
        self.log_panel.append("等待选择日语字幕 MKV…")

    def _build_local_engine(self, parent):
        tk.Frame(parent, bg=STROKE_SOFT, height=1).pack(fill="x", padx=12, pady=(12, 0))

        row = tk.Frame(parent, bg=LAYER)
        row.pack(fill="x", padx=12, pady=12)

        left = tk.Frame(row, bg=LAYER)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left, text="翻译模型", bg=LAYER, fg=TEXT,
            font=(FONT_UI, 10), anchor="w",
        ).pack(fill="x")

        tk.Label(
            left, text="本地 GGUF · llama.cpp", bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 8), anchor="w",
        ).pack(fill="x", pady=(4, 0))

        self.model_box = tk.Frame(
            row, bg=BG, cursor="hand2",
            highlightthickness=1, highlightbackground=STROKE,
        )
        self.model_box.pack(side="right", padx=(14, 0))

        inner = tk.Frame(self.model_box, bg=BG)
        inner.pack(fill="x")

        self.model_label = tk.Label(
            inner, text=self.model_var.get(), bg=BG, fg=TEXT,
            font=(FONT_MONO, 8), anchor="w", cursor="hand2",
        )
        self.model_label.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=7)

        self.model_arrow = tk.Label(
            inner, text="▾", bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 8), cursor="hand2",
        )
        self.model_arrow.pack(side="right", padx=(0, 10))

        self.model_size = tk.Label(
            inner, text="", bg=BG, fg=TEXT_3,
            font=(FONT_MONO, 7), cursor="hand2",
        )
        self.model_size.pack(side="right", padx=(0, 8))

        for widget in (self.model_box, inner, self.model_label,
                       self.model_size, self.model_arrow):
            widget.bind("<Button-1>", self._show_model_menu)

        self._refresh_model_size()

    def _build_api_engine(self, parent):
        tk.Frame(parent, bg=STROKE_SOFT, height=1).pack(fill="x", padx=12, pady=(10, 0))

        self._field_row(parent, "API URL", self.api_url_var, divider=True)
        self._field_row(parent, "API KEY", self.api_key_var, show="*", divider=True)
        self._field_row(parent, "MODEL", self.api_model_var, divider=False)

    def _field_row(self, parent, label, variable, show=None, divider=True):
        row = tk.Frame(parent, bg=LAYER)
        row.pack(fill="x", padx=12, pady=8)

        tk.Label(
            row, text=label, bg=LAYER, fg=TEXT_3,
            font=(FONT_MONO, 7, "bold"), width=9, anchor="w",
        ).pack(side="left")

        box = tk.Frame(row, bg=BG, highlightthickness=1, highlightbackground=STROKE)
        box.pack(side="left", fill="x", expand=True, padx=(10, 0))

        entry = tk.Entry(
            box, textvariable=variable, bg=BG, fg=TEXT,
            insertbackground=ACCENT, relief="flat", bd=0,
            highlightthickness=0, font=(FONT_MONO, 9), show=show or "",
        )
        entry.pack(fill="x", padx=9, pady=5)

        entry.bind("<FocusIn>", lambda _e: box.configure(highlightbackground=ACCENT_DIM))
        entry.bind(
            "<FocusOut>",
            lambda _e: (
                box.configure(highlightbackground=STROKE),
                self._save_translation_config(),
            ),
        )

        if divider:
            tk.Frame(parent, bg=STROKE_SOFT, height=1).pack(fill="x", padx=12)

    def _refresh_model_size(self):
        if not self._alive(self.model_size):
            return

        path = MODEL_DIR / self.model_var.get()
        text = human_size(path) if path.exists() else "MISSING"
        self.model_size.configure(
            text=text,
            fg=TEXT_3 if path.exists() else DANGER,
        )

    def _engine_changed(self, value, save=True):
        self.engine_var.set(value)

        if self._alive(self.segmented):
            self.segmented.set(value)

        if self._alive(getattr(self, "engine_note", None)):
            self.engine_note.configure(
                text=(
                    "VULKAN · 4096 CTX · OFFLINE"
                    if value == "local"
                    else "OPENAI COMPATIBLE · HTTP"
                )
            )

        if value == "local":
            self.api_frame.pack_forget()
            self.local_frame.pack(fill="x")
        else:
            self.local_frame.pack_forget()
            self.api_frame.pack(fill="x")

        if save:
            self._save_translation_config()

    def _save_translation_config(self):
        try:
            config = self._read_config()
            translation = config.setdefault("translation", {})
            translation["engine"] = self.engine_var.get()
            translation["model"] = self.model_var.get()
            translation["api_url"] = self.api_url_var.get()
            translation["api_key"] = self.api_key_var.get()
            translation["api_model"] = self.api_model_var.get()
            self._write_config(config)
        except Exception:
            pass

    # --------------------------------------------------------
    # Model menu
    # --------------------------------------------------------

    def _show_model_menu(self, _event=None):
        if self._model_popup is not None:
            self._close_popup(self._model_popup)

        local = [path.name for path in self._local_models()]

        if local:
            names = local
            # 配置里指向的模型被删了，也列出来提醒一下
            if self.model_var.get() and self.model_var.get() not in names:
                names.insert(0, self.model_var.get())
        else:
            names = []

        # 末尾始终给一个入口：没有模型时是「去下载」，有模型时是「下载 / 更换」
        names.append(MANAGE_ENTRY)

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=STROKE)
        self._model_popup = popup

        width = max(380, self.model_box.winfo_width())
        row_height = 38
        # 管理入口上面有一条分隔线，高度要多留一点，否则最后一行会被切掉
        divider = 14 if (MANAGE_ENTRY in names and len(names) > 1) else 0
        height = min(len(names), 8) * row_height + 8 + divider

        x = self.model_box.winfo_rootx()
        y = self.model_box.winfo_rooty() + self.model_box.winfo_height() + 4
        popup.geometry(f"{width}x{height}+{x}+{y}")

        container = tk.Frame(popup, bg=LAYER)
        container.pack(fill="both", expand=True, padx=1, pady=1)

        has_models = bool(local)

        for name in names:
            if name == MANAGE_ENTRY:
                if has_models:
                    tk.Frame(container, bg=STROKE_SOFT, height=1).pack(
                        fill="x", padx=8, pady=(4, 2)
                    )
                self._model_menu_manage_row(container, popup, has_models)
                continue

            selected = name == self.model_var.get()
            exists = (MODEL_DIR / name).exists()

            row = tk.Frame(
                container, bg=LAYER_ALT if selected else LAYER, cursor="hand2",
            )
            row.pack(fill="x", padx=4, pady=2)

            mark = tk.Label(
                row, text="●" if selected else "○",
                bg=row["bg"], fg=ACCENT if selected else TEXT_3,
                font=(FONT_MONO, 7),
            )
            mark.pack(side="left", padx=(10, 8))

            label = tk.Label(
                row, text=name, bg=row["bg"],
                fg=TEXT if selected else TEXT_2,
                font=(FONT_MONO, 8), anchor="w",
            )
            label.pack(side="left", fill="x", expand=True, pady=10)

            size_text = human_size(MODEL_DIR / name) if exists else "MISSING"
            tk.Label(
                row, text=size_text, bg=row["bg"],
                fg=TEXT_3 if exists else DANGER,
                font=(FONT_MONO, 7),
            ).pack(side="right", padx=(0, 12))

            def choose(value=name, window=popup):
                self._select_model(value)
                self._close_popup(window)

            for widget in (row, mark, label):
                widget.bind("<Button-1>", lambda _e, fn=choose: fn())
                widget.bind(
                    "<Enter>",
                    lambda _e, r=row, m=mark, lb=label, s=selected:
                    self._model_hover(r, m, lb, True, s),
                )
                widget.bind(
                    "<Leave>",
                    lambda _e, r=row, m=mark, lb=label, s=selected:
                    self._model_hover(r, m, lb, False, s),
                )

        popup.bind("<Escape>", lambda _e: self._close_popup(popup))
        popup.bind("<Button-1>", lambda _e, w=popup: self._close_popup(w))

        try:
            popup.grab_set()
        except tk.TclError:
            pass

        popup.focus_force()

    def _model_menu_manage_row(self, container, popup, has_models):
        row = tk.Frame(container, bg=LAYER, cursor="hand2")
        row.pack(fill="x", padx=4, pady=2)

        text = "下载 / 更换模型…" if has_models else "翻译模型未安装 · 点击下载"
        label = tk.Label(
            row, text=text,
            bg=LAYER, fg=ACCENT,
            font=(FONT_MONO, 8), anchor="w",
        )
        label.pack(side="left", fill="x", expand=True, padx=(10, 12), pady=10)

        def open_dialog(window=popup):
            self._close_popup(window)
            missing = models_setup.missing() or models_setup.REQUIRED
            self.after(10, lambda: ModelDialog(self, missing, kind="translate"))

        for widget in (row, label):
            widget.bind("<Button-1>", lambda _e, fn=open_dialog: fn())
            widget.bind(
                "<Enter>",
                lambda _e, r=row, lb=label: (
                    r.configure(bg=LAYER_ALT),
                    lb.configure(bg=LAYER_ALT),
                ),
            )
            widget.bind(
                "<Leave>",
                lambda _e, r=row, lb=label: (
                    r.configure(bg=LAYER),
                    lb.configure(bg=LAYER),
                ),
            )

    def _close_popup(self, popup):
        try:
            if popup.winfo_exists():
                popup.destroy()
        except tk.TclError:
            pass

    def _model_hover(self, row, mark, label, hover, selected):
        try:
            if not row.winfo_exists():
                return

            background = LAYER_ALT if (hover or selected) else LAYER

            row.configure(bg=background)
            mark.configure(bg=background)
            label.configure(bg=background, fg=TEXT if (hover or selected) else TEXT_2)
        except tk.TclError:
            return

    def _select_model(self, name):
        if name.startswith("("):
            return

        self.model_var.set(name)

        if self._alive(self.model_label):
            self.model_label.configure(text=name)

        self._refresh_model_size()
        self._save_translation_config()

    # --------------------------------------------------------
    # Pickers
    # --------------------------------------------------------

    def select_video(self):
        file_path = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.avi *.mov *.webm *.flv"),
                ("所有文件", "*.*"),
            ],
        )

        if not file_path:
            return

        self.selected_video = Path(file_path)

        if self._alive(self.video_name):
            self.video_name.configure(text=self.selected_video.name, fg=TEXT)

        if self._alive(self.video_meta):
            self.video_meta.configure(text=human_size(self.selected_video))

        self._log("")
        self._log(f"已选择：{self.selected_video.name}")

    def select_mkv(self):
        file_path = filedialog.askopenfilename(
            title="选择日语字幕 MKV",
            filetypes=[
                ("MKV 视频", "*.mkv"),
                ("所有文件", "*.*"),
            ],
        )

        if not file_path:
            return

        self.selected_mkv = Path(file_path)

        if self._alive(self.mkv_name):
            self.mkv_name.configure(text=self.selected_mkv.name, fg=TEXT)

        if self._alive(self.mkv_meta):
            self.mkv_meta.configure(text=human_size(self.selected_mkv))

        self._log("")
        self._log(f"已选择：{self.selected_mkv.name}")

    # --------------------------------------------------------
    # Job runner
    # --------------------------------------------------------

    def _start_current(self):
        if self.current_page == "japanese":
            self.start_japanese()
        elif self.current_page == "translate":
            self.start_translation()

    def _guard_start(self):
        if self.busy:
            messagebox.showwarning(
                "任务进行中",
                f"正在运行「{self.job_name}」，请等待完成。",
            )
            return False

        if not MAIN_PY.exists():
            messagebox.showerror("错误", f"找不到 main.py：\n{MAIN_PY}")
            return False

        return True

    def _launch(self, name, command, on_line):
        self.busy = True
        self.job_name = name
        self._active_log = self.log_panel

        self.status.set("running")
        self._log("")
        self._log(">>> " + " ".join(str(part) for part in command[1:]))

        thread = threading.Thread(
            target=self._stream, args=(command, on_line), daemon=True,
        )
        thread.start()

    def _stream(self, command, on_line):
        flags = paths.quiet_flags()
        tail = []

        # 子进程默认按系统代码页（中文 Windows 是 cp936）输出，这里统一成 UTF-8，
        # 和下面的 decoding 对齐，否则日志里的中文全是问号。
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
                env=env,
            )

            if process.stdout is not None:
                for raw in process.stdout:
                    line = raw.rstrip()

                    if line:
                        tail.append(line)
                        tail = tail[-30:]
                        self._log(line)

                    try:
                        on_line(line)
                    except Exception:
                        pass

            code = process.wait()

            if code != 0:
                raise RuntimeError(f"进程退出，代码 {code}")

            self._ui(lambda: self._job_done(True, "", ""))

        except Exception as exc:
            # 不把 traceback 甩给用户，挑一句能看懂的原因；
            # 完整堆栈已经逐行进日志区了。
            reason = explain_failure(tail)
            self._ui(lambda: self._job_done(False, reason, ""))

    def _job_done(self, ok, message, detail=""):
        self.busy = False
        self._active_log = None

        if self.status is not None:
            self.status.set("done" if ok else "error")

        if self._alive(self.start_pill):
            self.start_pill.set_state("normal")
            self.start_pill.set_text(
                "开始处理" if self.current_page == "japanese" else "开始翻译"
            )

        if ok:
            self._apply_progress(100, "完成")
            self._log("")
            self._log("=== 完成 ===")
            messagebox.showinfo("完成", f"{self.job_name}完成。")
            return

        self._fail_running_stage()
        self._log("")
        self._log(f"错误：{message}")

        text = f"{message}\n\n完整日志见下方日志区。"
        if detail:
            text = f"{message}\n\n{detail}"

        messagebox.showerror(f"{self.job_name}失败", text)

    # --------------------------------------------------------
    # Progress / log plumbing
    # --------------------------------------------------------

    def _apply_progress(self, value, text=None):
        if self._alive(self.progress):
            self.progress.set(value)

        if self._alive(self.progress_value):
            self.progress_value.configure(text=f"{int(round(value))}%")

        if text is not None and self._alive(self.progress_text):
            self.progress_text.configure(text=text)

    def _set_progress(self, value, text=None):
        self._ui(lambda: self._apply_progress(value, text))

    def _log(self, message, panel=None):
        target = panel or self._active_log or self.log_panel
        if target is None:
            return
        self._ui(lambda: target.append(message))

    # --------------------------------------------------------
    # Japanese pipeline
    # --------------------------------------------------------

    def start_japanese(self):
        if not self._guard_start():
            return

        if self.selected_video is None:
            messagebox.showwarning("提示", "请先选择视频文件。")
            return

        if self._alive(self.start_pill):
            self.start_pill.set_state("disabled")
            self.start_pill.set_text("处理中…")

        self._reset_stages()
        self._apply_progress(0, "启动…")

        self._launch(
            "日语字幕",
            paths.script_command("main.py", self.selected_video),
            self._on_pipeline_line,
        )

    def _on_pipeline_line(self, line):
        if "=== 第 1 步" in line:
            self._ui(lambda: self._set_stage(0, "run"))
            self._set_progress(2, "语音识别…")

        elif "[WHISPER_PROGRESS]" in line:
            try:
                payload = line.split("[WHISPER_PROGRESS]", 1)[1].strip()
                current, total = payload.split("/")
                ratio = int(current) / max(1, int(total))
                self._set_progress(2 + ratio * 76, f"Whisper {current} / {total}")
            except Exception:
                pass

        elif "=== 第 2 步" in line:
            self._ui(lambda: (self._set_stage(0, "done"), self._set_stage(1, "run")))
            self._set_progress(80, "生成 SRT…")

        elif "=== 第 3 步" in line:
            self._ui(lambda: (self._set_stage(1, "done"), self._set_stage(2, "run")))
            self._set_progress(90, "封装 MKV…")

        elif "=== 封装完成 ===" in line:
            self._set_progress(97, "写入文件…")

    # --------------------------------------------------------
    # Translation job
    # --------------------------------------------------------

    def start_translation(self):
        if not self._guard_start():
            return

        if self.selected_mkv is None:
            messagebox.showwarning("提示", "请先选择日语字幕 MKV。")
            return

        if self._alive(self.start_pill):
            self.start_pill.set_state("disabled")
            self.start_pill.set_text("翻译中…")

        self._apply_progress(0, "启动…")

        self._launch(
            "字幕翻译",
            paths.script_command("main.py", "--translate", self.selected_mkv),
            self._on_translate_line,
        )

    def _on_translate_line(self, line):
        if "=== 第 1 步" in line:
            self._set_progress(5, "提取日文字幕…")

        elif "=== 第 2 步" in line:
            self._set_progress(10, "启动翻译引擎…")

        elif "[TRANSLATE_PROGRESS]" in line:
            try:
                payload = line.split("[TRANSLATE_PROGRESS]", 1)[1].strip()
                current, total = payload.split("/")
                ratio = int(current) / max(1, int(total))
                self._set_progress(12 + ratio * 68, f"翻译 {current} / {total}")
            except Exception:
                pass

        elif "=== 第 4 步" in line or "=== 添加中文字幕" in line:
            self._set_progress(88, "封装双语 MKV…")

        elif "双字幕封装完成" in line:
            self._set_progress(96, "写入文件…")


if __name__ == "__main__":
    app = SubtitleTool()
    app.mainloop()
