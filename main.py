from pathlib import Path
import subprocess
import sys
import shutil
import json

import paths

ROOT = paths.RESOURCE_DIR

APP_DATA = paths.APP_DATA
TEMP_DIR = paths.TEMP_DIR

FFMPEG = paths.FFMPEG_EXE
LLAMA_SERVER = paths.LLAMA_EXE

CONFIG_FILE = paths.CONFIG_FILE


def load_config():

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"找不到配置文件：{CONFIG_FILE}"
        )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)



def get_translation_model():

    config = load_config()

    model_name = (
        config["translation"]["model"]
    )

    model_path = (
        paths.USER_MODEL_DIR
        / model_name
    )

    if not model_path.exists():
        raise FileNotFoundError(
            f"找不到翻译模型：{model_path}"
        )

    return model_path


# ============================================================
# Basic
# ============================================================

def clean_temp():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

def start_llama_server():

    print()
    print("=== 启动本地翻译引擎 ===")

    if not LLAMA_SERVER.exists():
        raise FileNotFoundError(
            f"找不到 llama-server：{LLAMA_SERVER}"
        )

    translation_model = (
        get_translation_model()
    )

    print(
        f"翻译模型：{translation_model.name}"
    )

    process = subprocess.Popen(
        [
            str(LLAMA_SERVER),

            "-m",
            str(translation_model),

            "--device",
            "Vulkan0",

            "-ngl",
            "all",

            "--jinja",

            "--reasoning", "off",

            "-fa",
            "on",

            "-c",
            "4096",

            "-np",
            "1",
        ],
        cwd=str(LLAMA_SERVER.parent),
        creationflags=paths.quiet_flags(),
    )

    print(
        f"llama-server PID：{process.pid}"
    )

    return process

def wait_for_llama_server(process):
    import time
    import urllib.request

    print("等待本地翻译引擎启动...")

    url = "http://127.0.0.1:8080/health"

    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(
                "llama-server 启动失败。"
            )

        try:
            with urllib.request.urlopen(
                url,
                timeout=1
            ):
                print("本地翻译引擎已就绪")
                return
        except Exception:
            time.sleep(1)

    process.terminate()

    raise RuntimeError(
        "等待 llama-server 启动超时。"
    )


def run_command(command):
    print()
    print(">>>", " ".join(map(str, command)))

    # 必须显式传 stdout/stderr：打包成 windowed exe 后，标准句柄不是「可继承」的，
    # 不传的话子进程（transcribe / srt_builder / mux）的输出会被静默丢弃，
    # 界面日志一片空白，whisper 的进度也就永远不动。
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        creationflags=paths.quiet_flags(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"命令执行失败，退出代码：{result.returncode}"
        )


# ============================================================
# Japanese subtitle
# ============================================================

def generate_japanese_subtitle(input_video):

    input_video = Path(input_video)

    print()
    print("========================================")
    print("       日语字幕生成")
    print("========================================")

    clean_temp()

    srt_file = (
        TEMP_DIR
        / f"{input_video.stem}.ja.srt"
    )

    ja_mkv_file = (
        input_video.parent
        / f"{input_video.stem}.ja.mkv"
    )

    temp_ja_mkv = (
        TEMP_DIR
        / f"{input_video.stem}.ja.mkv"
    )

    # --------------------------------------------------------
    # Step 1
    # --------------------------------------------------------

    print()
    print("=== 第 1 步：语音识别 ===")

    run_command(
        paths.script_command("transcribe.py", input_video)
    )

    # --------------------------------------------------------
    # Step 2
    # --------------------------------------------------------

    print()
    print("=== 第 2 步：生成 SRT ===")

    run_command(
        paths.script_command("srt_builder.py", input_video)
    )

    if not srt_file.exists():
        raise RuntimeError(
            f"没有找到字幕文件：{srt_file}"
        )

    # --------------------------------------------------------
    # Step 3
    # --------------------------------------------------------

    print()
    print("=== 第 3 步：封装 MKV ===")

    run_command(
        paths.script_command(
            "mux.py", input_video, srt_file, temp_ja_mkv,
        )
    )

    if not temp_ja_mkv.exists():
        raise RuntimeError(
            f"没有找到 MKV 文件：{temp_ja_mkv}"
        )

    shutil.copy2(
        temp_ja_mkv,
        ja_mkv_file
    )

    print()
    print("=== 封装完成 ===")
    print()
    print(f"日语字幕文件：")
    print(ja_mkv_file)

    # --------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------

    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

    print()
    print("临时文件已清理")
    print()
    print("=== 日语字幕流程完成 ===")


# ============================================================
# Translation
# ============================================================

def translate_subtitle(input_mkv):

    input_mkv = Path(input_mkv).resolve()

    print()
    print("========================================")
    print("       日中字幕翻译")
    print("========================================")

    clean_temp()

    llama_process = None

    try:

        config = load_config()

        translation_config = config.get(
            "translation",
            {}
        )

        engine = translation_config.get(
            "engine",
            "local"
        )

        if engine not in ("local", "api"):
            raise RuntimeError(
                f"未知的翻译引擎：{engine}"
            )

        print()
        print("=== 第 1 步：提取日文字幕 ===")

        srt_file = (
            TEMP_DIR
            / f"{input_mkv.stem}.ja.srt"
        )

        extract_command = [
            str(FFMPEG),
            "-y",
            "-i",
            str(input_mkv),
            "-map",
            "0:s:0",
            "-c:s",
            "srt",
            str(srt_file),
        ]

        run_command(extract_command)

        if not srt_file.exists():
            raise RuntimeError(
                "没有找到日文字幕轨道。"
            )

        if engine == "local":

            print()
            print("=== 第 2 步：启动本地翻译引擎 ===")

            llama_process = start_llama_server()

            wait_for_llama_server(
                llama_process
            )

        else:

            print()
            print("=== 第 2 步：使用 API 翻译引擎 ===")

            api_url = translation_config.get(
                "api_url",
                ""
            )

            api_model = translation_config.get(
                "api_model",
                ""
            )

            if not api_url:
                raise RuntimeError(
                    "API 模式没有设置 API URL。"
                )

            if not api_model:
                raise RuntimeError(
                    "API 模式没有设置 MODEL。"
                )

            print(
                f"API：{api_url}"
            )

            print(
                f"模型：{api_model}"
            )

        print()
        print("=== 第 3 步：翻译中文字幕 ===")

        run_command(
            paths.script_command("translate.py", srt_file)
        )

        zh_srt_file = (
            TEMP_DIR
            / f"{input_mkv.stem}.zh.srt"
        )

        if not zh_srt_file.exists():
            raise RuntimeError(
                f"没有找到中文字幕：{zh_srt_file}"
            )

        print()
        print("=== 第 4 步：加入中文字幕 ===")

        temp_final_mkv = (
            TEMP_DIR
            / f"{input_mkv.stem}.final.mkv"
        )

        run_command(
            paths.script_command(
                "mux.py", "--add-subtitle",
                input_mkv, zh_srt_file, temp_final_mkv,
            )
        )

        if not temp_final_mkv.exists():
            raise RuntimeError(
                f"没有找到最终文件：{temp_final_mkv}"
            )

        if input_mkv.name.endswith(".ja.mkv"):

            output_name = (
                input_mkv.name[:-7]
                + ".ja.zh.mkv"
            )

        else:

            output_name = (
                input_mkv.stem
                + ".ja.zh.mkv"
            )

        output_file = (
            input_mkv.parent
            / output_name
        )

        shutil.copy2(
            temp_final_mkv,
            output_file
        )

        print()
        print("=== 翻译封装完成 ===")
        print()
        print("日中双语文件：")
        print(output_file)

    finally:

        if llama_process is not None:

            print()
            print("=== 关闭本地翻译引擎 ===")

            if llama_process.poll() is None:

                llama_process.terminate()

                try:
                    llama_process.wait(
                        timeout=10
                    )

                except subprocess.TimeoutExpired:

                    print(
                        "llama-server 未能正常退出，强制关闭。"
                    )

                    llama_process.kill()

            print("本地翻译引擎已关闭")

        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)

        print()
        print("临时文件已清理")
        print()
        print("=== 字幕翻译流程完成 ===")

if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("用法：")
        print("  python main.py <视频文件>")
        print("  python main.py --translate <日语字幕 MKV>")
        sys.exit(1)

    if sys.argv[1] == "--translate":

        if len(sys.argv) < 3:
            print(
                "错误：缺少日语字幕 MKV 文件。"
            )
            sys.exit(1)

        translate_subtitle(
            sys.argv[2]
        )

    else:

        generate_japanese_subtitle(
            sys.argv[1]
        )