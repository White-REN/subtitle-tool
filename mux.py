from pathlib import Path
import subprocess
import sys

import paths

ROOT = paths.RESOURCE_DIR

APP_DATA = paths.APP_DATA

FFMPEG = paths.FFMPEG_EXE


def mux_to_mkv(input_video, subtitle_file, output_file):

    input_video = Path(input_video)
    subtitle_file = Path(subtitle_file)
    output_file = Path(output_file)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=== MKV 封装 ===")
    print(f"视频：{input_video}")
    print(f"字幕：{subtitle_file}")
    print(f"输出：{output_file}")

    cmd = [
        str(FFMPEG),
        "-y",

        "-i", str(input_video),
        "-i", str(subtitle_file),

        "-map", "0:v",
        "-map", "0:a?",
        "-map", "1:0",

        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", "srt",

        "-metadata:s:s:0", "language=jpn",
        "-metadata:s:s:0", "title=Japanese",

        "-disposition:s:0", "default",

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
        raise RuntimeError("MKV 封装失败")

    if not output_file.exists():
        raise RuntimeError(
            f"MKV 文件没有生成：{output_file}"
        )

    print()
    print("=== 封装完成 ===")
    print(f"MKV：{output_file}")

    return output_file

def add_second_subtitle(
    input_mkv,
    subtitle_file,
    output_file,
):
    input_mkv = Path(input_mkv)
    subtitle_file = Path(subtitle_file)
    output_file = Path(output_file)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=== 添加中文字幕 ===")
    print(f"输入 MKV：{input_mkv}")
    print(f"中文字幕：{subtitle_file}")
    print(f"输出：{output_file}")

    cmd = [
        str(FFMPEG),
        "-y",

        "-i", str(input_mkv),
        "-i", str(subtitle_file),

        "-map", "0",
        "-map", "1:0",

        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", "copy",

        "-metadata:s:s:1",
        "language=zho",

        "-metadata:s:s:1",
        "title=Chinese",

        "-disposition:s:0",
        "default",

        "-disposition:s:1",
        "default",

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
        raise RuntimeError(
            "双字幕 MKV 封装失败"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"双字幕 MKV 没有生成：{output_file}"
        )

    print()
    print("=== 双字幕封装完成 ===")
    print(f"MKV：{output_file}")

    return output_file


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("用法：")
        print(
            'python mux.py "视频文件" "字幕文件" "输出MKV"'
        )
        print(
            'python mux.py --add-subtitle '
            '"输入MKV" "中文字幕" "输出MKV"'
        )
        sys.exit(1)

    # =========================
    # 添加第二字幕
    # =========================

    if sys.argv[1] == "--add-subtitle":

        if len(sys.argv) < 5:
            print(
                '用法：python mux.py --add-subtitle '
                '"输入MKV" "中文字幕" "输出MKV"'
            )
            sys.exit(1)

        input_mkv = Path(sys.argv[2])
        subtitle_file = Path(sys.argv[3])
        output_file = Path(sys.argv[4])

        if not input_mkv.exists():
            raise FileNotFoundError(
                f"找不到输入 MKV：{input_mkv}"
            )

        if not subtitle_file.exists():
            raise FileNotFoundError(
                f"找不到中文字幕：{subtitle_file}"
            )

        add_second_subtitle(
            input_mkv,
            subtitle_file,
            output_file,
        )

    # =========================
    # 普通字幕封装
    # =========================

    else:

        if len(sys.argv) < 4:
            print(
                '用法：python mux.py '
                '"视频" "字幕" "输出MKV"'
            )
            sys.exit(1)

        input_video = Path(sys.argv[1])
        subtitle_file = Path(sys.argv[2])
        output_file = Path(sys.argv[3])

        if not input_video.exists():
            raise FileNotFoundError(
                f"找不到视频：{input_video}"
            )

        if not subtitle_file.exists():
            raise FileNotFoundError(
                f"找不到字幕文件：{subtitle_file}"
            )

        mux_to_mkv(
            input_video,
            subtitle_file,
            output_file,
        )
