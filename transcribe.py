from pathlib import Path
import subprocess
import re
import json
import sys


import paths

ROOT = paths.RESOURCE_DIR

APP_DATA = paths.APP_DATA

TEMP = paths.TEMP_DIR

AUDIO = TEMP / "audio.wav"

WHISPER_DIR = TEMP / "whisper"

MANIFEST_FILE = WHISPER_DIR / "manifest.json"

FFMPEG = paths.FFMPEG_EXE

WHISPER_RUNTIME = paths.WHISPER_DIR

WHISPER_EXE = paths.WHISPER_EXE

VAD_EXE = paths.WHISPER_VAD_EXE

MODELS = paths.BUNDLED_MODEL_DIR

VAD_MODEL = paths.VAD_MODEL

WHISPER_MODEL = paths.WHISPER_MODEL


# =========================
# 0. 从视频提取完整音频
# =========================

def extract_full_audio(input_video):
    print("\n=== 提取音频 ===")
    print(f"输入：{input_video}")
    print(f"输出：{AUDIO}")

    cmd = [
        str(FFMPEG),
        "-y",
        "-i", str(input_video),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(AUDIO),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=paths.quiet_flags(),
    )

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("FFmpeg 提取音频失败")

    if not AUDIO.exists():
        raise RuntimeError(
            f"音频文件没有生成：{AUDIO}"
        )

    print("音频提取完成")


# =========================
# 1. Silero VAD
# =========================

def run_vad():
    print("\n=== Silero VAD ===")

    cmd = [
        str(VAD_EXE),
        "-vm", str(VAD_MODEL),
        "-f", str(AUDIO),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=paths.quiet_flags(),
    )

    segments = []

    output = result.stdout + "\n" + result.stderr

    for line in output.splitlines():
        m = re.search(
            r"VAD segment\s+\d+:\s+start\s*=\s*([\d.]+),\s*end\s*=\s*([\d.]+)",
            line,
        )

        if m:
            start = int(float(m.group(1)) * 1000)
            end = int(float(m.group(2)) * 1000)
            segments.append((start, end))

    print(f"检测到 {len(segments)} 个语音区域")

    for i, (start, end) in enumerate(segments):
        print(
            f"{i:02d}: "
            f"{start / 1000:.3f}s -> "
            f"{end / 1000:.3f}s"
        )

    return segments


# =========================
# 2. 合并相邻 VAD 区域
# =========================

def merge_segments(segments, max_gap_ms=1000):
    if not segments:
        return []

    merged = [segments[0]]

    for start, end in segments[1:]:
        last_start, last_end = merged[-1]

        gap = start - last_end

        if gap <= max_gap_ms:
            merged[-1] = (
                last_start,
                max(last_end, end),
            )
        else:
            merged.append((start, end))

    return merged


# =========================
# 3. 生成 Whisper 上下文窗口
# =========================

def build_windows(
    segments,
    context_pad_ms=2000,
    max_window_ms=12000,
):
    windows = []

    for speech_start, speech_end in segments:

        start = max(
            0,
            speech_start - context_pad_ms
        )

        end = speech_end + context_pad_ms

        if end - start <= max_window_ms:
            windows.append(
                (start, end)
            )
            continue

        current_start = start

        while current_start < end:

            current_end = min(
                current_start + max_window_ms,
                end
            )

            windows.append(
                (current_start, current_end)
            )

            current_start = current_end

    return windows


# =========================
# 4. 提取音频窗口
# =========================

def extract_audio(start_ms, end_ms, output_file):
    duration_ms = end_ms - start_ms

    start_seconds = start_ms / 1000
    duration_seconds = duration_ms / 1000

    cmd = [
        str(FFMPEG),
        "-y",
        "-ss", str(start_seconds),
        "-i", str(AUDIO),
        "-t", str(duration_seconds),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_file),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=paths.quiet_flags(),
    )

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("FFmpeg 提取音频失败")


# =========================
# 5. Whisper
# =========================

def run_whisper(audio_file, output_prefix):

    cmd = [
        str(WHISPER_EXE),

        "-m", str(WHISPER_MODEL),
        "-f", str(audio_file),

        "-l", "ja",

        # JSON
        "-ojf",

        # 输出文件
        "-of", str(output_prefix),
    ]

    print("运行 Whisper...")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=paths.quiet_flags(),
    )

    print(result.stdout)

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("Whisper 执行失败")

    json_file = Path(str(output_prefix) + ".json")

    if not json_file.exists():
        raise RuntimeError(
            f"没有找到 Whisper JSON：{json_file}"
        )

    return json_file


# =========================
# 6. 主程序
# =========================

if __name__ == "__main__":

    # ---------------------
    # 检查输入视频
    # ---------------------

    if len(sys.argv) < 2:
        print("用法：")
        print(
            'python transcribe.py ".\\input\\视频文件.mp4"'
        )
        sys.exit(1)

    input_video = Path(sys.argv[1])

    if not input_video.exists():
        raise FileNotFoundError(
            f"找不到视频文件：{input_video}"
        )

    if not input_video.is_file():
        raise RuntimeError(
            f"输入路径不是文件：{input_video}"
        )

    # ---------------------
    # 创建目录
    # ---------------------

    WHISPER_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------
    # 提取完整音频
    # ---------------------

    extract_full_audio(input_video)

    # ---------------------
    # VAD
    # ---------------------

    segments = run_vad()

    if not segments:
        raise RuntimeError(
            "没有检测到语音区域"
        )

    # ---------------------
    # 合并
    # ---------------------

    print("\n=== 合并语音区域 ===")

    merged = merge_segments(segments)

    print(
        f"合并后剩余 {len(merged)} 个区域"
    )

    for i, (start, end) in enumerate(merged):
        print(
            f"{i:02d}: "
            f"{start / 1000:.3f}s -> "
            f"{end / 1000:.3f}s"
        )

    # ---------------------
    # 窗口
    # ---------------------

    print("\n=== Whisper 上下文窗口 ===")

    windows = build_windows(
        merged,
        context_pad_ms=2000,
        max_window_ms=12000,
    )

    print(
        f"总共生成 {len(windows)} 个窗口"
    )

    # ---------------------
    # Whisper
    # ---------------------

    print(
        f"\n=== Whisper："
        f"{len(windows)} 个窗口 ==="
    )

    for i, (start, end) in enumerate(windows):

        print()
        print(
            f"--- Window {i:03d} ---"
        )

        print(
            f"时间："
            f"{start / 1000:.3f}s -> "
            f"{end / 1000:.3f}s"
        )

        # 临时 WAV
        audio_file = (
            WHISPER_DIR /
            f"window_{i:03d}.wav"
        )

        # JSON 前缀
        output_prefix = (
            WHISPER_DIR /
            f"window_{i:03d}"
        )

        # 提取音频
        print("提取音频...")

        extract_audio(
            start,
            end,
            audio_file,
        )

        # Whisper
        json_file = run_whisper(
            audio_file,
            output_prefix,
        )

        print(
            f"JSON：{json_file}"
        )

        print(
            f"[WHISPER_PROGRESS] "
            f"{i + 1}/{len(windows)}",
            flush=True
        )

    # ---------------------
    # Manifest
    # ---------------------

    manifest = []

    for i, (window_start, window_end) in enumerate(windows):

        core_segments = []

        for speech_start, speech_end in merged:

            if (
                speech_end > window_start
                and speech_start < window_end
            ):
                core_segments.append({
                    "start": speech_start,
                    "end": speech_end,
                })

        manifest.append({
            "index": i,
            "window_start": window_start,
            "window_end": window_end,
            "json": f"window_{i:03d}.json",
            "core_segments": core_segments,
        })

    with open(
        MANIFEST_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            manifest,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print(f"Manifest：{MANIFEST_FILE}")

    print()
    print("=== 转录完成 ===")
    print(
        f"结果目录：{WHISPER_DIR}"
    )
