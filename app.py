"""Subtitle Tool 统一入口。

正常启动 GUI；带 ``--internal-run <脚本>`` 时以子进程方式执行流水线的某个
步骤（transcribe / srt_builder / mux / translate / main）。

打包成 exe 之后，``sys.executable`` 指向 exe 本身，原来
``[sys.executable, "transcribe.py", ...]`` 的写法会变成再开一个 GUI，
所以统一改成走这个入口分派。
"""

import io
import runpy
import sys
import traceback

import paths


def force_utf8_stdio():
    """把子进程的标准输出强制成 UTF-8。

    父进程按 UTF-8 读管道；打包后的 exe 默认用系统代码页（中文 Windows 是
    cp936）写，中文日志会变成乱码。reconfigure 在冻结环境里不一定生效，
    所以再兜一层：直接拿底层 buffer 重新包一个 UTF-8 的 TextIOWrapper。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue

        try:
            # 必须同时开行缓冲：父进程按行读管道来显示实时日志和进度，
            # 块缓冲会导致整段输出卡到进程结束才一次性冒出来。
            stream.reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )
            continue
        except (AttributeError, ValueError):
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue

        try:
            setattr(
                sys, name,
                io.TextIOWrapper(
                    buffer, encoding="utf-8", errors="replace",
                    line_buffering=True, write_through=True,
                ),
            )
        except Exception:
            pass


def run_internal(script, args):
    """以 __main__ 身份执行流水线脚本，等价于 `python <script> <args>`。"""
    target = paths.script_path(script)

    if not target.exists():
        print(f"找不到脚本：{target}", file=sys.stderr)
        return 2

    force_utf8_stdio()

    sys.argv = [script] + list(args)

    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    return 0


def main():
    """所有未捕获异常都在这里落地。

    windowed exe 里如果让异常一路冒到解释器外，PyInstaller 的启动器会弹一个
    traceback 对话框然后挂在那里等用户点确定——父进程读不到退出码，界面就一直
    「处理中」。这里自己接住、把堆栈打到 stderr（父进程会捕获并显示在日志区）、
    返回非零退出码，进程干净退出。
    """
    try:
        if len(sys.argv) > 2 and sys.argv[1] == "--internal-run":
            return run_internal(sys.argv[2], sys.argv[3:])

        paths.ensure_dirs()
        paths.migrate_config()

        import gui

        gui.SubtitleTool().mainloop()
        return 0

    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
