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


def fill_gaps(segments, min_gap_ms=8000, chunk_ms=20000):
    """把过长的「非语音」空洞也补成待识别区段。

    Silero VAD 在有背景音乐的对白上会失效。实测 46 分钟的正片，VAD 只
    标出 61% 的音频，剩下 39% 里最长一段有 2 分钟——而那 2 分钟里是
    完整的对话，whisper 直接识别就能出句子。

    更麻烦的是它的结果还跟切片起点有关：同一段音频，从 2000 秒开始切
    就整段漏掉，从 2100 秒开始切就正常检出。所以不能只靠 VAD 决定
    「哪里有人说话」。

    做法：空洞超过 min_gap_ms 就当成可疑区域，切成 chunk_ms 的块交给
    whisper。纯音乐/静音的部分 whisper 会返回空，后面自然会被过滤掉。
    """

    if not segments:
        return segments

    filled = list(segments)

    for index in range(len(segments) - 1):

        end = segments[index][1]
        next_start = segments[index + 1][0]

        if next_start - end < min_gap_ms:
            continue

        cursor = end

        while cursor < next_start:

            chunk_end = min(
                cursor + chunk_ms,
                next_start,
            )

            filled.append((cursor, chunk_end))
            cursor = chunk_end

    filled.sort()

    return filled


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

    # VAD 漏掉的对白：把长空洞也补进来，交给 whisper 判断
    filled = fill_gaps(merged)

    if len(filled) > len(merged):
        print(
            f"补充 {len(filled) - len(merged)} 个疑似区域"
            f"（VAD 在背景音乐下会漏检）"
        )

    merged = filled

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
