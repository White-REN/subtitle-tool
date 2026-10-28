"""翻译模型的检测与下载。

安装包里只带字幕模型（whisper large-v3-turbo + silero VAD，约 1.6GB）。
翻译模型体积太大（Q4_K_M 约 4.6GB），不打进安装包，首次启动时由程序处理：

- 已经手动放好 → 直接可用
- 配置了下载地址 → 显示「自动下载」
- 都没配 → 提示文件应该放在哪，并提供「打开文件夹」

下载地址填在下面 REQUIRED 的 url 字段即可，支持 HTTP Range 断点续传。
"""

import hashlib
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import paths


# 四个档位，按显存推荐。只做建议——用户可以在对话框里选别的，
# 也可以自己准备任意 gguf 丢进目录。
#
# 文件来自 HuggingFace 的官方仓库 tencent/Hy-MT2-{7B,1.8B}-GGUF，
# sha256 用的是 HF 的 lfs.oid（就是文件内容的 sha256，已拿本地 Q4 校验过）。
# hf-mirror.com 是国内镜像，主站连不上时会自动切换。
HF_7B = "https://huggingface.co/tencent/Hy-MT2-7B-GGUF/resolve/main"
HF_18B = "https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main"
MIRROR_7B = "https://hf-mirror.com/tencent/Hy-MT2-7B-GGUF/resolve/main"
MIRROR_18B = "https://hf-mirror.com/tencent/Hy-MT2-1.8B-GGUF/resolve/main"

REQUIRED = [
    {
        "key": "small_q8",
        "filename": "Hy-MT2-1.8B-Q8_0.gguf",
        "size": 1908528192,
        "url": f"{HF_18B}/Hy-MT2-1.8B-Q8_0.gguf",
        "mirrors": [f"{MIRROR_18B}/Hy-MT2-1.8B-Q8_0.gguf"],
        "sha256": "5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        "label": "1.8B · Q8_0",
        "tier": "CPU",
        "note": "没有可用的独显",
    },
    {
        "key": "small_q4",
        "filename": "Hy-MT2-1.8B-Q4_K_M.gguf",
        "size": 1133080448,
        "url": f"{HF_18B}/Hy-MT2-1.8B-Q4_K_M.gguf",
        "mirrors": [f"{MIRROR_18B}/Hy-MT2-1.8B-Q4_K_M.gguf"],
        "sha256": "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
        "label": "1.8B · Q4_K_M",
        "tier": "< 8GB",
        "note": "显存不足 8GB",
    },
    {
        "key": "q4",
        "filename": "Hy-MT2-7B-Q4_K_M.gguf",
        "size": 4624648896,
        "url": f"{HF_7B}/Hy-MT2-7B-Q4_K_M.gguf",
        "mirrors": [f"{MIRROR_7B}/Hy-MT2-7B-Q4_K_M.gguf"],
        "sha256": "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        "label": "7B · Q4_K_M",
        "tier": "8GB",
        "note": "速度与质量平衡",
    },
    {
        "key": "q6",
        "filename": "HY-MT2-7B-Q6_K.gguf",
        "size": 6164482720,
        "url": f"{HF_7B}/HY-MT2-7B-Q6_K.gguf",
        "mirrors": [f"{MIRROR_7B}/HY-MT2-7B-Q6_K.gguf"],
        "sha256": "88ef0aba59952a4cfe4be36cb5baf797dbb370bc60e9dcbd7297036021e52831",
        "label": "7B · Q6_K",
        "tier": "12GB+",
        "note": "翻译质量最好，占显存多",
    },
]

# 字幕模型（whisper + VAD）由单独的「字幕模型包」安装，不在主程序安装包里。
# 缺了就没法做语音识别，所以要单独检测、单独提示。
BUNDLED = [
    {"filename": "ggml-large-v3-turbo.bin", "note": "语音识别模型 · 约 1.6GB"},
    {"filename": "ggml-silero-v6.2.0.bin", "note": "语音检测模型 · 约 0.9MB"},
]


_DEVICE_RE = re.compile(
    r"^\s*(?:Vulkan|CUDA)\d+:\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)"
)


def detect_device(timeout=15):
    """用 llama-server 自己枚举设备，返回显存最大的那块。

    比查 WMI 靠谱：WMI 的 AdapterRAM 在 4GB 处就截断，也拿不到实际可用显存；
    这里用的是我们真正做推理的同一个运行时，结果就是真实可用值。
    """
    exe = paths.LLAMA_EXE
    if not exe.exists():
        return None

    try:
        result = subprocess.run(
            [str(exe), "--list-devices"],
            cwd=str(exe.parent),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=paths.quiet_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None

    best = None
    for line in (result.stdout or "").splitlines():
        match = _DEVICE_RE.match(line)
        if not match:
            continue

        total_mib = int(match.group(2))
        if best is None or total_mib > best["total_mib"]:
            best = {
                "name": match.group(1).strip(),
                "total_mib": total_mib,
                "free_mib": int(match.group(3)),
                "total_gb": round(total_mib / 1024, 1),
            }

    return best


def recommend():
    """按硬件挑一档。返回 (推荐条目, 设备信息或 None)。

    只是建议——用户可以在对话框里选别的，也可以自己丢一个 gguf 进目录。
    """
    device = detect_device()

    if device is None:
        return by_key("small_q8"), None

    # 用 MiB 和「厂商标称 GB」比：8098 MiB 的卡是 8GB 卡，按 GiB 算只有 7.9 会被误判
    vram = device["total_mib"]
    if vram >= 12 * 1000:
        return by_key("q6"), device
    if vram >= 8 * 1000:
        return by_key("q4"), device
    return by_key("small_q4"), device


def by_key(key):
    for item in REQUIRED:
        if item.get("key") == key:
            return item
    return None


def missing_bundled():
    """随字幕模型包分发的文件，缺了哪些。"""
    return [
        item for item in BUNDLED
        if not (paths.BUNDLED_MODEL_DIR / item["filename"]).exists()
    ]


def open_bundled_folder():
    directory = bundled_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        os.startfile(str(directory))
    except (OSError, AttributeError):
        pass


def bundled_dir():
    return paths.BUNDLED_MODEL_DIR


def target_dir():
    return paths.USER_MODEL_DIR


def missing():
    """返回缺失的模型条目。"""
    result = []
    for item in REQUIRED:
        if not (target_dir() / item["filename"]).exists():
            result.append(item)
    return result


def installed_models():
    """目录里现有的 gguf（用户可能放了别的量化版本）。"""
    try:
        return sorted(
            path.name for path in target_dir().iterdir()
            if path.is_file() and path.suffix.lower() == ".gguf"
        )
    except OSError:
        return []


def has_any_model():
    return bool(installed_models())


def downloadable():
    return [item for item in missing() if item.get("url")]


def open_folder():
    directory = target_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        os.startfile(str(directory))
    except (OSError, AttributeError):
        pass


def download(item, on_progress=None, cancelled=None, chunk=256 * 1024):
    """下载单个模型，返回保存路径。

    支持断点续传：目标文件已存在时用 Range 请求接着下。
    on_progress(done, total) 会在主循环之外被调用，调用方自己负责切回 UI 线程。
    cancelled() 返回 True 时中止。
    """
    url = item.get("url")
    if not url:
        raise ValueError("没有配置下载地址")

    dest = target_dir() / item["filename"]
    dest.parent.mkdir(parents=True, exist_ok=True)

    part = dest.with_suffix(dest.suffix + ".part")
    start_at = part.stat().st_size if part.exists() else 0
    total = item.get("size") or 0

    # 磁盘空间预检：4.3GB 下到一半发现没空间，比直接拒绝更糟
    need = max(0, total - start_at) if total else 0
    if need:
        free = shutil.disk_usage(dest.parent).free
        if free < need * 1.05:
            raise OSError(
                f"磁盘空间不足：还需要 {need / 2**30:.1f} GB，"
                f"当前只剩 {free / 2**30:.1f} GB"
            )

    # 主站连不上就换镜像（国内访问 huggingface.co 经常不通）
    candidates = [url] + [u for u in item.get("mirrors", []) if u]
    last_error = None

    for index, candidate in enumerate(candidates):
        # 每次重试都按 .part 的当前长度续传
        start_at = part.stat().st_size if part.exists() else 0

        request = urllib.request.Request(candidate)
        if start_at:
            request.add_header("Range", f"bytes={start_at}-")

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if start_at and response.status != 206:
                    # 服务端不支持续传，从头来
                    start_at = 0
                    part.unlink(missing_ok=True)

                length = int(response.headers.get("Content-Length") or 0)
                if length:
                    total = start_at + length

                mode = "ab" if start_at else "wb"
                with part.open(mode) as handle:
                    done = start_at
                    while True:
                        if cancelled is not None and cancelled():
                            raise InterruptedError("已取消")

                        block = response.read(chunk)
                        if not block:
                            break

                        handle.write(block)
                        done += len(block)

                        if on_progress is not None:
                            on_progress(done, total or done)

            last_error = None
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if index + 1 < len(candidates):
                continue
            raise

    if last_error is not None:
        raise last_error

    part.replace(dest)

    expected = (item.get("sha256") or "").strip().lower()
    if expected:
        actual = file_sha256(dest)
        if actual != expected:
            dest.unlink(missing_ok=True)
            raise ValueError(
                "下载的文件校验不通过，可能传输中断或源文件已变更。\n"
                f"期望 {expected[:16]}…，实际 {actual[:16]}…"
            )

    return dest


def file_sha256(path, chunk=8 * 1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
