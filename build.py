"""一键打包：PyInstaller 出 onedir，再用 Inno Setup 编译安装包。

用法：
    python build.py                  # 完整构建（程序 + 安装包）
    python build.py --app-only       # 只打程序，跳过安装包
    python build.py --console        # 打带控制台的调试版
    python build.py --skip-models    # 安装包里不含字幕模型（调试用，快很多）
    python build.py --iscc D:\\ISCC.exe

产物：
    dist/SubtitleTool/                     程序本体
    installer/output/SubtitleTool-x.y.z-Setup.exe
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "subtitle-tool.spec"
DIST = ROOT / "dist" / "SubtitleTool"
ISS = ROOT / "installer" / "subtitle-tool.iss"
RUNTIME = ROOT / "runtime"
MODELS = ROOT / "models"
OUTPUT_DIR = ROOT / "installer" / "output"

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
]


def log(message):
    print(f"[build] {message}", flush=True)


def find_iscc(explicit=None):
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise SystemExit(f"指定的 ISCC 不存在：{path}")

    for path in ISCC_CANDIDATES:
        if path.is_file():
            return path

    raise SystemExit(
        "找不到 Inno Setup 的 ISCC.exe。\n"
        "装一个：winget install JRSoftware.InnoSetup\n"
        "或者用 --iscc 指定路径。"
    )


def resolve_runtime(explicit=None):
    r"""找到 runtime/（ffmpeg + llama + whisper）。

    优先项目根目录；开发机上它通常在 %LOCALAPPDATA%\SubtitleTool\runtime，
    那就退回那里，免得每次都要拷一份。
    """
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise SystemExit(f"指定的 runtime 目录不存在：{path}")
        return path

    if RUNTIME.is_dir():
        return RUNTIME

    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "SubtitleTool" / "runtime"
    if fallback.is_dir():
        log(f"项目里没有 runtime/，改用 {fallback}")
        return fallback

    raise SystemExit(
        "找不到 runtime/（ffmpeg / llama / whisper）。\n"
        "把它放到项目根目录，或者用 --runtime 指定路径。\n"
        "获取方式见 README 的「构建」一节。"
    )


def check_models():
    """安装包里要带 whisper 模型，缺了就别往下走。"""
    needed = ["ggml-large-v3-turbo.bin", "ggml-silero-v6.2.0.bin"]
    missing = [name for name in needed if not (MODELS / name).exists()]
    if missing:
        raise SystemExit(
            f"models/ 里缺少：{'、'.join(missing)}\n"
            "这两个文件会打进安装包，见 README 的「构建」一节。"
        )


def clean_dist():
    """清掉上一次的产物。

    删不掉时退一步改名，而不是直接失败——程序还在运行、杀软占用、沙箱拦截
    都会导致删不掉，但改名几乎总能成功，构建不至于卡在这一步。
    """
    if not DIST.exists():
        return

    log(f"清理 {DIST}")
    try:
        shutil.rmtree(DIST)
        return
    except OSError:
        pass

    stale = DIST.with_name(f"_stale_{int(time.time())}")
    try:
        DIST.rename(stale)
        log(f"删不掉（多半是程序还在运行），已改名到 {stale.name}，可手动清理")
    except OSError as exc:
        raise SystemExit(
            f"既删不掉也改不了名：{DIST}\n"
            f"请先关闭正在运行的 SubtitleTool.exe 再试。\n（{exc}）"
        )


def build_app(console=False):
    env = os.environ.copy()
    env["ST_CONSOLE"] = "1" if console else "0"

    log(f"PyInstaller 打包中（{'调试版，带控制台' if console else '正式版'}）…")
    started = time.time()

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)],
        cwd=str(ROOT),
        env=env,
    )

    if result.returncode != 0:
        raise SystemExit("PyInstaller 失败，见上面的日志。")

    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    log(f"程序打包完成：{DIST}  ({size / 1024 / 1024:.0f} MB, {time.time() - started:.0f}s)")


def build_installer(iscc=None, runtime_src=None):
    """编译安装包。

    字幕模型（1.6GB）打在里面，所以这一步要几分钟——大部分时间花在压缩
    whisper 模型上。改代码时如果只想验证程序本身，用 --app-only。
    """
    iscc = find_iscc(iscc)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for old in OUTPUT_DIR.glob("*.exe"):
        old.unlink(missing_ok=True)

    log(f"编译安装包（{ISS.name}）…")
    started = time.time()

    runtime = resolve_runtime(runtime_src)
    result = subprocess.run(
        # 不要在值里自带引号：subprocess 会把它转义成 \"，Inno 收到的就是坏路径
        [str(iscc), f"/DRuntimeSrc={runtime}", str(ISS)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise SystemExit("安装包编译失败，见上面的日志。")

    setups = sorted(OUTPUT_DIR.glob("*.exe"), key=lambda p: p.stat().st_mtime)
    if not setups:
        raise SystemExit(f"没找到安装包，检查 {OUTPUT_DIR}")

    setup = setups[-1]
    size = setup.stat().st_size / 1024 / 1024 / 1024
    log(f"安装包完成：{setup.name}  ({size:.2f} GB, {time.time() - started:.0f}s)")
    return setup


def main():
    parser = argparse.ArgumentParser(description="打包 Subtitle Tool")
    parser.add_argument("--app-only", action="store_true", help="只打程序，不编译安装包")
    parser.add_argument("--console", action="store_true", help="打带控制台的调试版")
    parser.add_argument("--iscc", help="ISCC.exe 路径")
    parser.add_argument("--runtime", help="runtime 目录（ffmpeg / llama / whisper）")
    args = parser.parse_args()

    clean_dist()
    build_app(console=args.console)

    if args.app_only:
        log("跳过安装包（--app-only）")
        return

    check_models()
    build_installer(iscc=args.iscc, runtime_src=args.runtime)


if __name__ == "__main__":
    main()
