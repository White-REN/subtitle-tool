# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物是 onedir（SubtitleTool/ 目录），不是 onefile：
启动快 1~2 秒，也不会被杀软反复扫描解压。

设置环境变量 ST_CONSOLE=1 可以打一个带控制台的调试版本。
"""

import ast
import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
)


CONSOLE = os.environ.get("ST_CONSOLE", "0") == "1"

PIPELINE = [
    "main.py",
    "transcribe.py",
    "srt_builder.py",
    "mux.py",
    "translate.py",
]

# 流水线脚本要在打包后仍能被子进程用 runpy 执行，所以按数据文件随包分发。
# assets/logo.png 是窗口图标 + 左上角品牌图。
datas = [("assets/logo.png", "assets")] + [(name, ".") for name in PIPELINE]

# SudachiPy 的分词词典（system.dic，约 217MB）不会被自动收集，必须显式带上。
datas += collect_data_files("sudachidict_core")

# sudachipy 的 resources/（sudachi.json / char.def / rewrite.def / unk.def）是
# 运行时必需的配置，同样要显式收集。
datas += collect_data_files("sudachipy")


def scan_imports(paths):
    """静态扫描脚本的 import。

    上面 5 个脚本是以「数据文件」形式打包的，PyInstaller 不会分析它们，
    所以脚本里用到的模块必须手工声明。这里用 ast 扫一遍，以后脚本里新增
    依赖（包括函数内部的延迟 import，比如 difflib）也能自动带上。
    """
    found = set()
    for name in paths:
        tree = ast.parse(Path(name).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module:
                    found.add(node.module)
                    found.add(node.module.split(".")[0])
    return found


LOCAL_MODULES = {"paths", "models_setup"}

# sudachipy 的 __init__ 用 import_module 动态加载 dictionary / tokenizer 等子包，
# 静态分析抓不到，直接 collect_submodules 全收。
hiddenimports = sorted(scan_imports(PIPELINE) - LOCAL_MODULES)
hiddenimports += collect_submodules("sudachipy")
hiddenimports += ["sudachidict_core"]

hiddenimports += [
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.font",
]

# 本机装了一堆跟这个工具无关的包，显式排除，避免被误收集导致体积暴涨。
excludes = [
    "matplotlib", "scipy", "numpy", "PIL", "trimesh", "shapely",
    "rtree", "mapbox_earcut", "pymeshfix", "kiwisolver", "contourpy",
    "fonttools", "networkx", "pyparsing", "dateutil", "tzdata",
]


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    # 子进程崩了不要再弹一个 PyInstaller 的 traceback 窗口——它会把堆栈写到
    # stderr，被父进程捕获后显示在界面日志区，用户只需要看一句人话。
    disable_windowed_traceback=True,
    icon="assets/logo.png",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubtitleTool",
)
