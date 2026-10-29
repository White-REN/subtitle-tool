"""字幕质量检查。

用法：
    python check_srt.py <字幕.srt>

把字幕的可读性量化出来，改完断句逻辑跑一遍就知道是变好还是变坏。
"""

import re
import statistics
import sys
from pathlib import Path

# 日语字幕的通行标准
MAX_CHARS_PER_LINE = 20      # 单行不超过 20 字
MAX_CHARS_PER_SUB = 40       # 两行上限
MAX_CPS = 6.0                # 每秒字数，超过就来不及看
MIN_DURATION = 1.0
MAX_DURATION = 7.0

TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8")
    subtitles = []

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        match = TIME_RE.search(lines[1])
        if not match:
            continue

        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000

        subtitles.append({
            "start": start,
            "end": end,
            "text": "".join(lines[2:]),
            "lines": lines[2:],
        })

    return subtitles


def inspect(subtitles, video_seconds=None):
    if not subtitles:
        print("没有解析出任何字幕")
        return

    durations = [s["end"] - s["start"] for s in subtitles]
    lengths = [len(s["text"]) for s in subtitles]
    cps = [len(s["text"]) / max(0.1, s["end"] - s["start"]) for s in subtitles]
    line_counts = [len(s["lines"]) for s in subtitles]

    total = sum(durations)

    print(f"条数            {len(subtitles)}")
    if video_seconds:
        print(f"视频时长        {video_seconds:.0f}s")
        print(f"字幕覆盖        {total:.0f}s（{total / video_seconds * 100:.0f}%）")
    print(f"平均每条        {total / len(subtitles):.1f}s")

    print()
    print(f"字数  最短 {min(lengths):2d}  中位 {statistics.median(lengths):4.1f}  最长 {max(lengths):2d}")
    print(f"时长  最短 {min(durations):3.1f}  中位 {statistics.median(durations):4.1f}  最长 {max(durations):3.1f}")
    print(f"CPS   中位 {statistics.median(cps):4.1f}  最高 {max(cps):4.1f}")

    print()
    print("问题统计")
    checks = [
        (f"单条超过 {MAX_CHARS_PER_SUB} 字", sum(1 for n in lengths if n > MAX_CHARS_PER_SUB)),
        (f"单行超过 {MAX_CHARS_PER_LINE} 字", sum(1 for s in subtitles
                                              for line in s["lines"]
                                              if len(line) > MAX_CHARS_PER_LINE)),
        (f"CPS 超过 {MAX_CPS}（读不完）", sum(1 for c in cps if c > MAX_CPS)),
        (f"CPS 超过 {MAX_CPS * 2}（根本来不及）", sum(1 for c in cps if c > MAX_CPS * 2)),
        (f"时长不足 {MIN_DURATION}s", sum(1 for d in durations if d < MIN_DURATION - 0.01)),
        (f"时长超过 {MAX_DURATION}s", sum(1 for d in durations if d > MAX_DURATION + 0.01)),
        ("三行以上", sum(1 for n in line_counts if n > 2)),
        ("以标点开头", sum(1 for s in subtitles if s["text"][:1] in "、。，．,.")),
        ("以助词开头", sum(1 for s in subtitles
                          if s["text"][:1] in "はがをにへとのもや")),
    ]
    for label, count in checks:
        mark = "  " if count == 0 else "!!"
        print(f"  {mark} {label:26s} {count:3d} 条")

    overlaps = sum(
        1 for i in range(len(subtitles) - 1)
        if subtitles[i]["end"] > subtitles[i + 1]["start"] + 0.01
    )
    gaps = [
        subtitles[i + 1]["start"] - subtitles[i]["end"]
        for i in range(len(subtitles) - 1)
    ]
    print(f"  {'  ' if overlaps == 0 else '!!'} {'时间重叠':26s} {overlaps:3d} 处")
    print(f"  {'  ' if not [g for g in gaps if g > 2] else '!!'} "
          f"{'间隙超过 2s':26s} {sum(1 for g in gaps if g > 2):3d} 处"
          f"（最大 {max(gaps):.1f}s）" if gaps else "")

    print()
    print("最该修的几条")

    worst = sorted(
        subtitles,
        key=lambda s: len(s["text"]) / max(0.1, s["end"] - s["start"]),
        reverse=True,
    )[:5]
    for s in worst:
        dur = s["end"] - s["start"]
        print(f"  {dur:4.1f}s {len(s['text']):2d}字 CPS{len(s['text'])/max(0.1,dur):5.1f}  "
              f"{s['text'][:40]}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    path = Path(sys.argv[1])
    if not path.exists():
        raise SystemExit(f"找不到文件：{path}")

    video_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else None
    inspect(parse_srt(path), video_seconds)


if __name__ == "__main__":
    main()
