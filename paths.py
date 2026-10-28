"""统一的路径解析与子进程启动。

同一套代码要同时满足两种运行方式：

1. 源码运行：``python gui.py`` / ``python app.py``，脚本是磁盘上的 .py 文件。
2. 打包运行：PyInstaller 产出的 exe，脚本以数据文件形式随包分发。

两者的区别只有「只读资源在哪」和「怎么拉起子进程」，其余路径一律指向
``%LOCALAPPDATA%\\SubtitleTool``，保证装到 Program Files 之后仍然可写。
"""

import os
import subprocess
import sys
from pathlib import Path


FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # onefile 模式解压到临时目录，onedir 模式是 exe 同级目录下的 _internal
    RESOURCE_DIR = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
    )
    # exe 所在目录。runtime/ 和 models/ 这类「程序自带的大块资产」放在这里，
    # 而不是 _internal 里面——安装包把她们铺在 {app}\runtime、{app}\models。
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    APP_DIR = RESOURCE_DIR


# ------------------------------------------------------------
# 可写数据目录
# ------------------------------------------------------------

APP_DATA = Path.home() / "AppData" / "Local" / "SubtitleTool"

# 运行库（ffmpeg / llama / whisper）随安装包分发，优先用程序目录里的；
# 开发环境下程序目录没有 runtime/，自动回落到 %LOCALAPPDATA%。
_BUNDLED_RUNTIME = APP_DIR / "runtime"
RUNTIME_DIR = _BUNDLED_RUNTIME if _BUNDLED_RUNTIME.exists() else APP_DATA / "runtime"

FFMPEG_DIR = RUNTIME_DIR / "ffmpeg"
LLAMA_DIR = RUNTIME_DIR / "llama"
WHISPER_DIR = RUNTIME_DIR / "whisper"

# 字幕模型（whisper + VAD）随安装包分发
_BUNDLED_MODELS = APP_DIR / "models"
BUNDLED_MODEL_DIR = _BUNDLED_MODELS if _BUNDLED_MODELS.exists() else APP_DATA / "models"

# 翻译模型体积太大，不打进安装包，首次启动时下载到这里
USER_MODEL_DIR = APP_DATA / "models"

TEMP_DIR = APP_DATA / "temp"
CONFIG_FILE = APP_DATA / "config.json"


# ------------------------------------------------------------
# 只读资源
# ------------------------------------------------------------

ASSETS_DIR = RESOURCE_DIR / "assets"
LOGO_PATH = ASSETS_DIR / "logo.png"

FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
LLAMA_EXE = LLAMA_DIR / "llama-server.exe"
WHISPER_EXE = WHISPER_DIR / "whisper-cli.exe"
WHISPER_VAD_EXE = WHISPER_DIR / "whisper-vad-speech-segments.exe"

VAD_MODEL = BUNDLED_MODEL_DIR / "ggml-silero-v6.2.0.bin"
WHISPER_MODEL = BUNDLED_MODEL_DIR / "ggml-large-v3-turbo.bin"

PIPELINE_SCRIPTS = (
    "main.py",
    "transcribe.py",
    "srt_builder.py",
    "mux.py",
    "translate.py",
)


def ensure_dirs():
    """启动时把可写目录补齐。"""
    for path in (APP_DATA, TEMP_DIR, USER_MODEL_DIR):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def script_path(script):
    return RESOURCE_DIR / script


# CREATE_NO_WINDOW：0x08000000。打包后的 GUI 本身没有控制台，任何控制台子进程
# （python 脚本、ffmpeg、whisper-cli、llama-server）被拉起时 Windows 都会新建
# 一个黑窗口。必须显式关掉，否则整条流水线会一路弹终端。
CREATE_NO_WINDOW = 0x08000000


def quiet_flags():
    """给 subprocess 用的 creationflags，Windows 上禁止弹控制台窗口。"""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)


def script_command(script, *args):
    """构造启动流水线脚本的命令行。

    打包后 ``sys.executable`` 是 exe 自己，直接拼脚本路径会变成「再开一个
    GUI」，所以走 ``--internal-run`` 分派；仍然起独立进程，某一步崩掉不会
    连带界面一起挂。
    """
    tail = [str(arg) for arg in args]

    if FROZEN:
        return [sys.executable, "--internal-run", script] + tail

    return [sys.executable, str(RESOURCE_DIR / script)] + tail


def migrate_config():
    """把旧版本放在程序目录里的 config.json 搬到 APP_DATA。"""
    if CONFIG_FILE.exists():
        return

    legacy = RESOURCE_DIR / "config.json"
    if not legacy.exists() or legacy == CONFIG_FILE:
        return

    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_bytes(legacy.read_bytes())
    except OSError:
        pass
