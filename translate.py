from pathlib import Path
import json
import re
import sys
import urllib.request

import paths

ROOT = paths.RESOURCE_DIR

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

def translate_text(text):

    config = load_config()

    translation_config = (
        config["translation"]
    )

    engine = translation_config.get(
        "engine",
        "local"
    )

    api_url = translation_config.get(
        "api_url",
        "http://127.0.0.1:8080/v1/chat/completions"
    )

    api_key = translation_config.get(
        "api_key",
        ""
    )

    api_model = translation_config.get(
        "api_model",
        ""
    )

    model = translation_config.get(
        "model",
        "HY-MT2-7B-Q4_K_M.gguf"
    )

    prompt = (
        "将下面的日语字幕翻译成自然、准确、符合影视对白习惯的简体中文。\n"
        "只输出中文译文，不要解释，不要添加引号，不要输出日文。\n\n"
        f"日语：{text}"
    )

    if engine == "local":
        request_model = model

    elif engine == "api":

        if not api_model:
            raise RuntimeError(
                "API 模式没有设置 api_model。"
            )

        request_model = api_model

    else:

        raise RuntimeError(
            f"未知的翻译引擎：{engine}"
        )


    body = {
        "model": request_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.1,
        "top_p": 0.6,
        "max_tokens": 256,
        "stream": False,
    }


    data = json.dumps(
        body,
        ensure_ascii=False
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
    }

    if api_key:
        headers["Authorization"] = (
            f"Bearer {api_key}"
        )

    request = urllib.request.Request(
        api_url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=120,
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

    except Exception as e:

        raise RuntimeError(
            f"翻译 API 请求失败：{e}"
        )

    try:

        translated = (
            result["choices"][0]["message"]["content"]
            .strip()
        )

    except (
        KeyError,
        IndexError,
        TypeError
    ):

        raise RuntimeError(
            f"翻译 API 返回格式异常：\n{result}"
        )

    if not translated:

        raise RuntimeError(
            "翻译 API 返回了空译文"
        )

    return translated


def get_output_file(input_file):

    input_file = Path(input_file)

    if input_file.name.endswith(".ja.srt"):
        filename = (
            input_file.name[:-7]
            + ".zh.srt"
        )
    else:
        filename = (
            input_file.stem
            + ".zh.srt"
        )

    return (
        paths.TEMP_DIR
        / filename
    )


def translate_srt(input_file):

    input_file = Path(input_file)

    if not input_file.exists():
        raise FileNotFoundError(
            f"找不到字幕文件：{input_file}"
        )

    output_file = get_output_file(input_file)

    print("=" * 50)
    print("Subtitle Translator")
    print("=" * 50)
    print(f"输入：{input_file}")
    print(f"输出：{output_file}")
    print()

    text = input_file.read_text(
        encoding="utf-8-sig"
    )

    blocks = re.split(
        r"\r?\n\s*\r?\n",
        text.strip(),
    )

    output_blocks = []

    total = len(blocks)

    for index, block in enumerate(
        blocks,
        1,
    ):

        lines = block.splitlines()

        if len(lines) < 3:
            continue

        number = lines[0].strip()
        timeline = lines[1].strip()

        subtitle_text = "\n".join(
            lines[2:]
        ).strip()

        if not subtitle_text:
            output_blocks.append(block)
            continue

        print(
            f"[{index}/{total}] "
            f"{subtitle_text}"
        )

        translated = translate_text(
            subtitle_text
        )

        print(
            f"    → {translated}"
        )

        print(
            f"[TRANSLATE_PROGRESS] {index}/{total}",
            flush=True
        )
        
        output_blocks.append(
            "\n".join(
                [
                    number,
                    timeline,
                    translated,
                ]
            )
        )

    output_file.write_text(
        "\n\n".join(output_blocks) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 50)
    print("翻译完成")
    print("=" * 50)
    print(f"中文 SRT：{output_file}")


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("用法：")
        print(
            'python translate.py "日语字幕.ja.srt"'
        )
        sys.exit(1)

    translate_srt(
        sys.argv[1]
    )