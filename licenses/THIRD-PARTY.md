# 第三方组件

本程序（Subtitle Tool）以 GNU General Public License v3.0 分发，许可证全文见程序目录下的 `LICENSE`。

程序内嵌了以下第三方组件，各自的版权与许可如下。

## ffmpeg

- 版本：9.0.1-full_build（gyan.dev 构建，`--enable-gpl --enable-version3`）
- 许可：GPL-3.0
- 源码：<https://ffmpeg.org/download.html>
- 构建来源：<https://www.gyan.dev/ffmpeg/builds/>

ffmpeg 的源码可以从上述地址获取。本程序只使用了其中的解复用、封装（`-c copy`）和 `pcm_s16le` 编码功能。

## llama.cpp

- 用途：本地推理引擎（`llama-server.exe`）
- 许可：MIT
- 源码：<https://github.com/ggerganov/llama.cpp>

## whisper.cpp

- 用途：语音识别（`whisper-cli.exe`、`whisper-vad-speech-segments.exe`）
- 许可：MIT
- 源码：<https://github.com/ggerganov/whisper.cpp>

## SudachiPy / sudachidict_core

- 用途：日语形态素分析与断句
- 许可：Apache-2.0（与 GPL-3.0 兼容）
- 源码：<https://github.com/WorksApplications/SudachiPy>

## 模型文件

模型是数据，不是代码，不受 GPL 约束。

| 模型 | 许可 | 来源 |
|---|---|---|
| Whisper large-v3-turbo（ggml） | MIT | <https://huggingface.co/openai/whisper-large-v3-turbo> |
| Silero VAD | MIT | <https://github.com/snakers4/silero-vad> |
| Hunyuan-MT（Hy-MT2） | Apache-2.0 | <https://huggingface.co/tencent/Hy-MT2-7B-GGUF> |

Hunyuan-MT 的模型文件由用户按需下载，不随本程序分发。
