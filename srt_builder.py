from pathlib import Path
import json
import re
import sys

from sudachipy import dictionary


import paths

ROOT = paths.RESOURCE_DIR

WHISPER_DIR = paths.TEMP_DIR / "whisper"
MANIFEST_FILE = WHISPER_DIR / "manifest.json"

SRT_FILE = None

# ============================================================
# Sudachi
# ============================================================

tokenizer = dictionary.Dictionary().create()

# ============================================================
# 基本工具
# ============================================================

def clean_text(text):
    """清理 Whisper 文本中的特殊内容。"""

    if not text:
        return ""

    text = re.sub(r"\s+", "", text)

    # Whisper 特殊 token
    text = re.sub(r"\[_.*?\]", "", text)

    return text.strip()


def format_time(seconds):
    """秒 -> SRT 时间格式。"""

    ms = int(round(seconds * 1000))

    hours = ms // 3_600_000
    ms %= 3_600_000

    minutes = ms // 60_000
    ms %= 60_000

    seconds = ms // 1000
    ms %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d},"
        f"{ms:03d}"
    )


# ============================================================
# Whisper JSON
# ============================================================

def redistribute(text, start, end):
    """把一段文字按字符均分到 [start, end] 时间区间上。

    whisper 在长片段上偶尔会把除前几个之外的所有 token 都标成同一时刻
    （零长度）。这时候与其丢掉整段，不如按字数均分——时间不精确，
    但内容还在，观众至少看得到字幕。
    """

    chars = list(text)

    if not chars or end <= start:
        return []

    step = (end - start) / len(chars)

    return [
        {
            "text": char,
            "start": start + index * step,
            "end": start + (index + 1) * step,
        }
        for index, char in enumerate(chars)
    ]


def load_tokens(json_file):
    """读取 Whisper JSON 中的真实文本 token。"""

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    tokens = []

    for segment in data.get("transcription", []):

        raw = []

        for token in segment.get("tokens", []):

            text = token.get("text", "")

            if not text:
                continue

            # Whisper 特殊 token
            if text.startswith("[_") and text.endswith("]"):
                continue

            start = (
                token
                .get("offsets", {})
                .get("from", 0)
                / 1000
            )

            end = (
                token
                .get("offsets", {})
                .get("to", 0)
                / 1000
            )

            raw.append({
                "text": text,
                "start": start,
                "end": end,
            })

        if not raw:
            continue

        valid = [
            token for token in raw
            if token["end"] > token["start"]
        ]

        # ----------------------------------------------------
        # 时间戳退化检测
        #
        # 正常的 token 覆盖应该接近整段文字。如果有效 token 的文字量
        # 明显少于整段，说明 whisper 把大部分 token 标成了零长度，
        # 直接过滤会把整段内容丢掉（实测丢过一整句 12 秒的旁白）。
        # ----------------------------------------------------

        full_text = "".join(token["text"] for token in raw)
        valid_text = "".join(token["text"] for token in valid)

        if len(valid_text) < len(full_text) * 0.8:

            offsets = segment.get("offsets", {})

            tokens.extend(
                redistribute(
                    full_text,
                    offsets.get("from", 0) / 1000,
                    offsets.get("to", 0) / 1000,
                )
            )

        else:

            tokens.extend(valid)

    return tokens


# ============================================================
# Window -> Candidate
# ============================================================

def tokens_to_candidate(item, manifest_item):
    window_start = manifest_item["window_start"]

    core_segments = []
    for seg in manifest_item.get("core_segments", []):
        core_segments.append({
            "start": seg["start"],
            "end": seg["end"]
        })

    tokens = load_tokens(WHISPER_DIR / item["json"])

    if not tokens:
        return None
    

    text = clean_text(
        "".join(
            t["text"]
            for t in tokens
        )
    )

    if not text:
        return None

    abs_tokens = []

    for t in tokens:

        abs_start = (
            window_start / 1000
            + t["start"]
        )

        abs_end = (
            window_start / 1000
            + t["end"]
        )

        abs_tokens.append({
            "text": t["text"],
            "start": abs_start,
            "end": abs_end
        })

    start = min(
        t["start"]
        for t in abs_tokens
    )

    end = max(
        t["end"]
        for t in abs_tokens
    )

    return {
        "window": item["index"],
        "start": start,
        "end": end,
        "text": text,
        "tokens": abs_tokens,
        "core_segments": core_segments
    }

# ============================================================
# 文本相似度
# ============================================================

def text_similarity(a, b):
    """判断两个 Window 是否属于重复内容。"""

    a = clean_text(a)
    b = clean_text(b)

    if not a or not b:
        return False

    if a == b:
        return True

    if a in b or b in a:
        return True

    return False


# ============================================================
# Window 去重
# ============================================================

def find_duplicates(candidates):

    duplicates = set()

    for i in range(len(candidates)):

        a = candidates[i]

        if i in duplicates:
            continue

        for j in range(i + 1, len(candidates)):

            b = candidates[j]

            if j in duplicates:
                continue

            # ==================================================
            # 时间没有交集
            # ==================================================

            overlap_start = max(
                a["start"],
                b["start"]
            )

            overlap_end = min(
                a["end"],
                b["end"]
            )

            if overlap_end <= overlap_start:
                continue


            # ==================================================
            # 计算时间重叠比例
            # ==================================================

            overlap = (
                overlap_end
                - overlap_start
            )

            duration_a = (
                a["end"]
                - a["start"]
            )

            duration_b = (
                b["end"]
                - b["start"]
            )

            if duration_a <= 0 or duration_b <= 0:
                continue

            overlap_ratio_a = (
                overlap
                / duration_a
            )

            overlap_ratio_b = (
                overlap
                / duration_b
            )


            # ==================================================
            # 时间重叠太少
            # ==================================================

            if (
                overlap_ratio_a < 0.50
                and
                overlap_ratio_b < 0.50
            ):
                continue


            # ==================================================
            # 文本清理
            # ==================================================

            text_a = clean_text(
                a["text"]
            )

            text_b = clean_text(
                b["text"]
            )

            if not text_a or not text_b:
                continue


            # ==================================================
            # 判断是否是真正的重复
            #
            # 这里只处理：
            #
            # 1. 完全相同
            # 2. 一个完整包含另一个
            #
            # 不处理只有部分文字相同的情况。
            # ==================================================

            if text_a == text_b:

                if duration_a >= duration_b:
                    keep_index = i
                    remove_index = j
                else:
                    keep_index = j
                    remove_index = i

            elif text_a in text_b:

                keep_index = j
                remove_index = i

            elif text_b in text_a:

                keep_index = i
                remove_index = j

            else:

                continue


            # ==================================================
            # 输出
            # ==================================================

            keep = candidates[keep_index]
            remove = candidates[remove_index]

            print(
                f"发现重复："
                f"Window {a['window']:03d} "
                f"<-> "
                f"Window {b['window']:03d}"
            )

            print(
                f"  A："
                f"{a['start']:.2f}"
                f" -> "
                f"{a['end']:.2f} "
                f"「{text_a}」"
            )

            print(
                f"  B："
                f"{b['start']:.2f}"
                f" -> "
                f"{b['end']:.2f} "
                f"「{text_b}」"
            )

            print(
                f"  保留："
                f"candidate {keep_index} "
                f"(Window {keep['window']:03d})"
            )

            print(
                f"  删除："
                f"candidate {remove_index} "
                f"(Window {remove['window']:03d})"
            )


            # ==================================================
            # 只记录 candidate 下标
            #
            # 不再删除整个 Window
            # ==================================================

            duplicates.add(
                remove_index
            )


    return duplicates


# ============================================================
# Sudachi 分词
# ============================================================

def tokenize_words(tokens):

    # --------------------------------------------------------
    # 必须保留 Whisper 原始文本
    # 不能在这里 clean_text()
    # --------------------------------------------------------

    text = "".join(
        token["text"]
        for token in tokens
    )

    if not text:
        return []

    morphemes = tokenizer.tokenize(
        text
    )

    words = []

    cursor = 0

    for morpheme in morphemes:

        word = morpheme.surface()

        if not word:
            continue

        # ----------------------------------------------------
        # 不再简单使用：
        #
        #     start = cursor
        #     cursor += len(word)
        #
        # 而是在原始 Whisper 文本中寻找这个词。
        #
        # 这样可以自动跳过：
        #   空格
        #   换行
        #   其他 Sudachi 没有返回的字符
        # ----------------------------------------------------

        start_char = text.find(
            word,
            cursor,
        )

        # ----------------------------------------------------
        # 极端情况下找不到，跳过这个词
        # 不要制造错误的字符坐标
        # ----------------------------------------------------

        if start_char < 0:
            continue

        end_char = (
            start_char
            + len(word)
        )

        words.append({
            "text": word,
            "start_char": start_char,
            "end_char": end_char,
            "pos": morpheme.part_of_speech(),
        })

        cursor = end_char

    return words

# ============================================================
# 断点评分
# ============================================================

# ============================================================
# 断点相关词表
# ============================================================

# 句末标点：断在这里最自然
STRONG_END = {"。", "．", "？", "?", "！", "!", "…"}

# 读点：语气停顿，适合断
WEAK_END = {"、", "，", ","}

# 接续词：后面通常开始新的一层意思
CONTINUATIONS = {
    "しかし", "そして", "また", "ただ", "でも", "ところが", "つまり",
    "さらに", "なお", "一方", "ところで", "ちなみに", "ただし",
    "それでも", "だから", "ですから", "なぜなら", "たとえば",
    "すると", "そこで", "それに", "そのうえ", "および", "またしても",
}

# 助数词：前面是数字时不能切开（9 月、246 万 6000 人）
COUNTERS = {
    "月", "日", "年", "人", "回", "時", "分", "秒", "個", "件", "位",
    "歳", "度", "番", "号", "階", "軒", "台", "枚", "冊", "匹", "頭",
    "羽", "円", "名", "割", "倍", "周", "期", "時半",
}

# サ変動詞的后半截：名词 + する 不能切开（合祀 + する）
SURU_FORMS = {"する", "し", "した", "して", "される", "され", "させる", "でき"}

# 补助动词：て + いる / しまう 不能切开
AUX_VERBS = {"いる", "いた", "しまう", "しまった", "おく", "みる", "くる", "いく"}


def break_score(
    left_words,
    right_words,
    max_chars,
):

    left = left_words[-1]
    right = right_words[0]

    left_text = left["text"]
    right_text = right["text"]

    left_pos = left["pos"]
    right_pos = right["pos"]

    score = 0

    # --------------------------------------------------------
    # 标点：最强的断点信号
    #
    # 之前完全没有处理标点，导致「、」经常落在字幕中间，
    # 甚至跑到下一条的开头。
    # --------------------------------------------------------

    if left_text in STRONG_END:
        score += 150

    elif left_text in WEAK_END:
        score += 80

    # 标点不能出现在下一条开头
    if right_text in STRONG_END or right_text in WEAK_END:
        score -= 250

    # --------------------------------------------------------
    # 接续词：后面断，前面不断
    # --------------------------------------------------------

    if left_text in CONTINUATIONS:
        score += 90

    if right_text in CONTINUATIONS:
        score -= 150

    # --------------------------------------------------------
    # 数字 + 助数词 不能切开
    # --------------------------------------------------------

    is_number = "数詞" in left_pos or left_text.isdigit()
    if is_number and right_text in COUNTERS:
        score -= 250

    # --------------------------------------------------------
    # 名词 + する（サ変動詞）不能切开
    # --------------------------------------------------------

    if "名詞" in left_pos and right_text in SURU_FORMS:
        score -= 150

    # --------------------------------------------------------
    # て + 补助动词 不能切开
    # --------------------------------------------------------

    if left_text in {"て", "で"} and right_text in AUX_VERBS:
        score -= 150

    # --------------------------------------------------------
    # 强烈禁止：
    # 动词 + 助动词
    # --------------------------------------------------------

    if (
        "動詞" in left_pos
        and "助動詞" in right_pos
    ):
        score -= 100

    if (
        "助動詞" in left_pos
        and "助動詞" in right_pos
    ):
        score -= 100

    # --------------------------------------------------------
    # 助词不能放到下一句开头
    # --------------------------------------------------------

    if right_text in {
        "は", "が", "を", "に",
        "へ", "と", "で", "の",
        "も", "や", "から",
        "まで", "より",
    }:
        score -= 100

    # --------------------------------------------------------
    # 自然断点
    # --------------------------------------------------------

    if left_text in {
        "こと",
        "もの",
        "ところ",
        "ため",
        "わけ",
        "よう",
        "ので",
        "のに",
        "けど",
        "が",
        "し",
    }:
        score += 50

    # --------------------------------------------------------
    # 句尾是助动词（です / ます / た），句子大概率结束
    # --------------------------------------------------------

    if "助動詞" in left_pos:
        score += 30

    # --------------------------------------------------------
    # 名词后面通常比较容易断
    # --------------------------------------------------------

    if "名詞" in left_pos:
        score += 15

    # --------------------------------------------------------
    # 助词后可以作为弱断点
    # --------------------------------------------------------

    if "助詞" in left_pos:
        score += 5

    # --------------------------------------------------------
    # 前半不能太短
    # --------------------------------------------------------

    length = sum(
        len(word["text"])
        for word in left_words
    )

    if length < 5:
        score -= 30

    # --------------------------------------------------------
    # 接近目标长度
    # --------------------------------------------------------

    score += length * 0.5

    return score


# ============================================================
# Token -> 字幕组
# ============================================================

def split_tokens(
    tokens,
    max_chars=14,
):

    if not tokens:
        return []

    words = tokenize_words(tokens)

    if not words:
        return [tokens]

    groups = []

    start = 0

    while start < len(words):

        remaining_text = "".join(
            word["text"]
            for word in words[start:]
        )

        # 剩余内容可以全部放进字幕
        if len(remaining_text) <= max_chars:

            groups.append(
                words[start:]
            )

            break

        best_end = None
        best_score = -10_000

        current_length = 0

        for end in range(
            start,
            len(words),
        ):

            current_length += len(
                words[end]["text"]
            )

            if current_length > max_chars:
                break

            if current_length < 5:
                continue

            left_words = words[
                start:end + 1
            ]

            right_words = words[
                end + 1:
            ]

            if not right_words:
                continue

            score = break_score(
                left_words,
                right_words,
                max_chars,
            )

            if score > best_score:

                best_score = score
                best_end = end

        # 找到断点
        if best_end is not None:

            groups.append(
                words[
                    start:best_end + 1
                ]
            )

            start = best_end + 1

        else:

            # 兜底
            groups.append(
                words[start:start + 1]
            )

            start += 1

    # ========================================================
    # Sudachi word -> Whisper token
    # ========================================================

    # 先算出每个 token 在原文里的字符区间
    spans = []
    cursor = 0

    for token in tokens:
        token_text = token["text"]
        spans.append((token, cursor, cursor + len(token_text)))
        cursor += len(token_text)

    result = [[] for _ in groups]

    for token, token_start, _token_end in spans:

        # 空格 token 不参与字幕
        if not token["text"].strip():
            continue

        # 按 token 的「起点」归组，一个 token 只进一组。
        #
        # 之前的写法是判断区间有交集，结果跨组边界的 token 会被
        # 前后两组同时收下，文本就重复了——「されています」被切成
        # 两组时会出现「されてれています」这种鬼东西。
        for index, word_group in enumerate(groups):

            group_start = word_group[0]["start_char"]
            group_end = word_group[-1]["end_char"]

            if group_start <= token_start < group_end:
                result[index].append(token)
                break

    return [
        group for group in result
        if group
    ]


# ============================================================
# 字幕候选
# ============================================================

def group_to_subtitle(group):

    if not group:
        return None

    text = clean_text(
        "".join(
            token["text"]
            for token in group
        )
    )

    if not text:
        return None

    start = group[0]["start"]
    end = group[-1]["end"]

    return {
        "start": start,
        "end": end,
        "text": text,
    }

# ============================================================
# 字幕候选去重 / 重叠消解
# ============================================================

def subtitle_similarity(a, b):
    """判断两个字幕是否高度重复。"""

    ta = clean_text(a["text"])
    tb = clean_text(b["text"])

    if not ta or not tb:
        return 0.0

    if ta == tb:
        return 1.0

    # 一个完整包含另一个
    if ta in tb or tb in ta:
        shorter = min(len(ta), len(tb))
        longer = max(len(ta), len(tb))

        if shorter / longer >= 0.55:
            return 0.9

    # 字符集合/序列相似度
    from difflib import SequenceMatcher

    return SequenceMatcher(
        None,
        ta,
        tb,
    ).ratio()


def subtitle_overlap(a, b):
    """计算两个字幕的时间重叠比例。"""

    overlap_start = max(
        a["start"],
        b["start"],
    )

    overlap_end = min(
        a["end"],
        b["end"],
    )

    overlap = overlap_end - overlap_start

    if overlap <= 0:
        return 0.0

    duration_a = a["end"] - a["start"]
    duration_b = b["end"] - b["start"]

    if duration_a <= 0 or duration_b <= 0:
        return 0.0

    return overlap / min(
        duration_a,
        duration_b,
    )


def deduplicate_subtitles(subtitles):
    """
    对重叠 Whisper Window 产生的字幕候选进行去重。

    核心原则：

    1. 时间高度重叠 + 文本相同/包含/高度相似
       -> 只保留一个

    2. 如果候选带有 core_segments：
       -> 优先保留真正覆盖 VAD 核心语音区域更多的候选

    3. 文本更完整的候选优先

    4. 真正连续、文本不同的字幕不互相删除

    5. 过滤 Whisper 产生的纯符号垃圾字幕

    6. 处理 Window 边界产生的明显重复
    """

    if not subtitles:
        return []

    subtitles = sorted(
        subtitles,
        key=lambda x: (
            x["start"],
            x["end"],
        ),
    )

    def text_clean(text):
        return clean_text(text)

    def similarity(a, b):
        ta = text_clean(a["text"])
        tb = text_clean(b["text"])

        if not ta or not tb:
            return 0.0

        if ta == tb:
            return 1.0

        if ta in tb or tb in ta:
            shorter = min(
                len(ta),
                len(tb),
            )
            longer = max(
                len(ta),
                len(tb),
            )

            if longer > 0:
                return shorter / longer

        from difflib import SequenceMatcher

        return SequenceMatcher(
            None,
            ta,
            tb,
        ).ratio()

    def overlap_ratio(a, b):
        start = max(
            a["start"],
            b["start"],
        )

        end = min(
            a["end"],
            b["end"],
        )

        if end <= start:
            return 0.0

        overlap = end - start

        duration_a = (
            a["end"]
            - a["start"]
        )

        duration_b = (
            b["end"]
            - b["start"]
        )

        if duration_a <= 0 or duration_b <= 0:
            return 0.0

        return overlap / min(
            duration_a,
            duration_b,
        )

    def time_distance(a, b):
        if a["end"] < b["start"]:
            return (
                b["start"]
                - a["end"]
            )

        if b["end"] < a["start"]:
            return (
                a["start"]
                - b["end"]
            )

        return 0.0

    def core_coverage(item):
        core_segments = item.get(
            "core_segments",
            [],
        )

        if not core_segments:
            return 0.0

        start = item["start"]
        end = item["end"]

        duration = end - start

        if duration <= 0:
            return 0.0

        covered = 0.0

        for seg in core_segments:

            seg_start = (
                seg["start"]
                / 1000
            )

            seg_end = (
                seg["end"]
                / 1000
            )

            overlap_start = max(
                start,
                seg_start,
            )

            overlap_end = min(
                end,
                seg_end,
            )

            if overlap_end > overlap_start:
                covered += (
                    overlap_end
                    - overlap_start
                )

        return min(
            1.0,
            covered / duration,
        )

    def is_duplicate(a, b):

        ta = text_clean(
            a["text"]
        )

        tb = text_clean(
            b["text"]
        )

        if not ta or not tb:
            return False

        # ========================================================
        # 1. 真正的时间重叠
        # ========================================================

        overlap_start = max(
            a["start"],
            b["start"],
        )

        overlap_end = min(
            a["end"],
            b["end"],
        )

        if overlap_end > overlap_start:

            overlap_seconds = (
                overlap_end
                - overlap_start
            )

            # 完全相同
            if ta == tb:
                return (
                    overlap_seconds
                    >= 0.20
                )

            # 一方完整包含另一方
            if ta in tb or tb in ta:

                shorter_duration = min(
                    a["end"] - a["start"],
                    b["end"] - b["start"],
                )

                if shorter_duration <= 0:
                    return False

                shorter_overlap_ratio = (
                    overlap_seconds
                    / shorter_duration
                )

                if (
                    overlap_seconds >= 0.30
                    and
                    shorter_overlap_ratio >= 0.50
                ):
                    return True

                return False

            # 高度相似
            shorter_duration = min(
                a["end"] - a["start"],
                b["end"] - b["start"],
            )

            if shorter_duration <= 0:
                return False

            shorter_overlap_ratio = (
                overlap_seconds
                / shorter_duration
            )

            sim = similarity(
                a,
                b,
            )

            if (
                overlap_seconds >= 0.50
                and
                shorter_overlap_ratio >= 0.50
                and
                sim >= 0.82
            ):
                return True

            return False

        # ========================================================
        # 2. Window 边界重复
        # ========================================================

        if a["end"] <= b["start"]:

            gap = (
                b["start"]
                - a["end"]
            )

            previous = ta
            current = tb

        elif b["end"] <= a["start"]:

            gap = (
                a["start"]
                - b["end"]
            )

            previous = tb
            current = ta

        else:
            return False

        # 必须几乎无间隔
        if gap > 0.10:
            return False

        if not previous or not current:
            return False

        # 最多检查 8 个字符
        max_shared_chars = min(
            8,
            len(previous),
            len(current),
        )

        if max_shared_chars < 2:
            return False

        # 前一句结尾 == 后一句开头
        for length in range(
            max_shared_chars,
            1,
            -1,
        ):

            suffix = previous[
                -length:
            ]

            prefix = current[
                :length
            ]

            if suffix != prefix:
                continue

            if (
                length / len(current)
                >= 0.50
            ):

                print(
                    "发现边界重复："
                    f"「{previous}」"
                    f" -> "
                    f"「{current}」"
                    f" | 重复片段「{suffix}」"
                    f" | gap={gap:.3f}s"
                )

                return True

        return False

    def current_is_better(
        current,
        previous,
    ):

        current_text = text_clean(
            current["text"]
        )

        previous_text = text_clean(
            previous["text"]
        )

        current_core = core_coverage(
            current
        )

        previous_core = core_coverage(
            previous
        )

        if (
            current_core
            > previous_core + 0.15
        ):
            return True

        if (
            previous_core
            > current_core + 0.15
        ):
            return False

        if (
            len(current_text)
            != len(previous_text)
        ):
            return (
                len(current_text)
                > len(previous_text)
            )

        current_duration = (
            current["end"]
            - current["start"]
        )

        previous_duration = (
            previous["end"]
            - previous["start"]
        )

        if (
            current_duration
            != previous_duration
        ):
            return (
                current_duration
                > previous_duration
            )

        return (
            current["start"]
            < previous["start"]
        )

    result = []

    for current in subtitles:

        current_text = text_clean(
            current["text"]
        )

        if not current_text:
            continue

        # 过滤纯符号垃圾
        if current_text in {
            "-",
            "—",
            "_",
        }:
            continue

        duplicate_indexes = []

        for index, previous in enumerate(
            result
        ):

            distance = time_distance(
                previous,
                current,
            )

            if distance > 5.0:
                continue

            if is_duplicate(
                previous,
                current,
            ):
                duplicate_indexes.append(
                    index
                )

        if not duplicate_indexes:
            result.append(current)
            continue

        keep_current = True

        for index in duplicate_indexes:

            previous = result[index]

            if not current_is_better(
                current,
                previous,
            ):

                keep_current = False

                print(
                    "删除重复字幕："
                    f"「{current_text}」"
                    f" -> 保留"
                    f"「{text_clean(previous['text'])}」"
                )

                break

        if keep_current:

            for index in reversed(
                duplicate_indexes
            ):

                previous = result[index]

                print(
                    "替换重复字幕："
                    f"「{text_clean(previous['text'])}」"
                    f" -> "
                    f"「{current_text}」"
                )

                del result[index]

            result.append(current)

    result.sort(
        key=lambda x: (
            x["start"],
            x["end"],
        )
    )

    return result

# ============================================================
# 时间优化
# ============================================================

# whisper 在音乐、静音、片尾这些没有人声的地方，会凭空生成一批固定句子。
# 补洞之后这种情况会变多，必须滤掉，否则字幕里会冒出莫名其妙的「感谢观看」。
HALLUCINATIONS = {
    "ご視聴ありがとうございました",
    "ご視聴いただきありがとうございました",
    "最後までご視聴いただきありがとうございました",
    "ご視聴ありがとうございます",
    "チャンネル登録お願いします",
    "チャンネル登録よろしくお願いします",
    "字幕は自動生成されています",
    "自動字幕",
}


def is_hallucination(text):
    """判断是不是 whisper 的固定幻听。

    只匹配下面这几个完整片段——单独一个「ありがとうございました」
    是正常对白，不能误删。
    """

    normalized = re.sub(r"[\s、。，．!?！？「」…]", "", text)

    # 用「包含」而不是「整句相等」：幻听经常带前后缀，
    # 比如「残念で阿部ご視聴ありがとうございました」「…ありがとうございましたん」。
    # 这几个片段足够特殊，正常对白里不会出现。
    return any(
        pattern in normalized
        for pattern in HALLUCINATIONS
    )


def drop_hallucinations(subtitles):
    kept = [s for s in subtitles if not is_hallucination(s["text"])]

    removed = len(subtitles) - len(kept)

    if removed:
        print(f"滤掉 {removed} 条幻听字幕")

    return kept


def resolve_overlaps(subtitles):
    """消解相邻字幕的时间重叠。

    正常的字幕是首尾相接的（前一条结束 = 后一条开始），真正重叠的
    几乎都是相邻窗口把同一段语音转写了两遍，两条说的是一回事，
    一起显示只会闪来闪去。

    规则：重叠且文字相近 → 丢掉字数少的那条；
          重叠但内容确实不同 → 把前一条的结束时间收回来。
    """

    if not subtitles:
        return []

    subtitles = sorted(
        subtitles,
        key=lambda x: (x["start"], x["end"]),
    )

    result = [subtitles[0]]

    for current in subtitles[1:]:

        previous = result[-1]

        overlap = previous["end"] - current["start"]

        # 首尾相接不算重叠
        if overlap <= 0.05:
            result.append(current)
            continue

        previous_duration = max(0.1, previous["end"] - previous["start"])
        current_duration = max(0.1, current["end"] - current["start"])

        overlap_ratio = overlap / min(previous_duration, current_duration)

        same_line = (
            subtitle_similarity(previous, current) >= 0.35
            or previous["text"] in current["text"]
            or current["text"] in previous["text"]
        )

        if same_line and overlap_ratio >= 0.3:

            # 说的是同一句话，留信息量大的那条
            if len(current["text"]) > len(previous["text"]):
                result[-1] = current
            continue

        # 内容不同：把前一条收回来。
        # 收完如果短到看不清（不到 0.6 秒），说明它本来就是被挤出来的
        # 碎片，直接丢掉，别留一条一闪而过的字幕。
        trimmed_end = current["start"]

        if trimmed_end - previous["start"] < 0.6:
            result.pop()
        else:
            previous["end"] = trimmed_end

        result.append(current)

    return result


def optimize_timing(subtitles):
    if not subtitles:
        return []

    # 先按实际开始时间排序
    subtitles = sorted(
        subtitles,
        key=lambda x: (
            x["start"],
            x["end"],
        ),
    )

    MIN_DURATION = 1.0
    MAX_DURATION = 7.0

    # 每秒最多读几个字。日语字幕的通行区间是 4~6，
    # 超过 6 观众就跟不上了。用它反推「这条字幕至少得显示多久」，
    # 比固定 1.0 秒合理得多——14 个字塞进 1 秒根本看不清。
    MAX_CPS = 5.5

    result = []

    for subtitle in subtitles:

        start = subtitle["start"]
        end = subtitle["end"]

        text = subtitle["text"]
        duration = end - start

        # 按字数算出需要的显示时长
        needed = max(
            MIN_DURATION,
            len(text) / MAX_CPS,
        )

        # ----------------------------------------------------
        # 太短
        # ----------------------------------------------------

        if duration < needed:

            end = start + needed

        # ----------------------------------------------------
        # 太长
        # ----------------------------------------------------

        if end - start > MAX_DURATION:

            end = start + MAX_DURATION

        result.append({
            "start": start,
            "end": end,
            "text": text,
        })

    # --------------------------------------------------------
    # 防止字幕互相覆盖
    # --------------------------------------------------------
    for i in range(
        len(result) - 1
    ):
        current = result[i]
        next_sub = result[i + 1]

        if current["end"] > next_sub["start"]:
            current["end"] = max(
                current["start"] + 0.3,
                next_sub["start"],
            )

    return result

# ============================================================
# 字幕断句合并
# ============================================================

def merge_subtitles(subtitles):
    """
    合并因为字幕长度限制而产生的碎片字幕。

    主要目的：
    让一句完整的日语尽可能作为一个翻译单元。

    基本规则：
    1. 两条字幕之间距离很近
    2. 前一句没有明显结束标点
    3. 合并后长度不过长
    4. 后一句不是明显的新句子
    """

    if not subtitles:
        return []

    subtitles = sorted(
        subtitles,
        key=lambda x: (
            x["start"],
            x["end"],
        ),
    )

    result = []

    # 合并后的最大字符数
    #
    # 日语字幕单行 20 字是通行上限，超过就得折行或者顶到屏幕边缘。
    # 之前是 32（为了让翻译拿到完整句子），但代价是 28/38 条字幕
    # 都超宽。翻译单元应该另外处理，显示先按标准来。
    MAX_CHARS = 20

    # 两条字幕之间最多允许的间隔
    MAX_GAP = 0.35

    # 明显表示一句话结束
    END_MARKS = {
        "。",
        "？",
        "?",
        "！",
        "!",
        "。",
        "」",
        "』",
        "）",
        ")",
        "…",
        "。",
    }

    # 明显不能作为句子开头的字符
    CONTINUE_START = {
        "は",
        "が",
        "を",
        "に",
        "へ",
        "と",
        "で",
        "の",
        "も",
        "や",
        "から",
        "まで",
        "より",
        "って",
        "て",
        "で",
        "し",
        "けど",
        "けれど",
        "ので",
        "のに",
    }

    def should_merge(previous, current):

        previous_text = previous["text"].strip()
        current_text = current["text"].strip()

        if not previous_text or not current_text:
            return False

        # ----------------------------------------------------
        # 时间间隔
        # ----------------------------------------------------

        gap = (
            current["start"]
            - previous["end"]
        )

        if gap < 0:
            gap = 0

        if gap > MAX_GAP:
            return False

        # ----------------------------------------------------
        # 合并后长度
        # ----------------------------------------------------

        merged_text = (
            previous_text
            + current_text
        )

        if len(merged_text) > MAX_CHARS:
            return False

        # ----------------------------------------------------
        # 前一句已经明显结束
        # ----------------------------------------------------

        if previous_text[-1] in END_MARKS:
            return False

        # ----------------------------------------------------
        # 后一句明显是前一句的继续
        # ----------------------------------------------------

        for prefix in CONTINUE_START:
            if current_text.startswith(prefix):
                return True

        # ----------------------------------------------------
        # 日语通常没有空格。
        #
        # 如果前一句没有结束标点，
        # 而且时间几乎连续，
        # 默认认为它可能还是同一句。
        # ----------------------------------------------------

        return True

    for current in subtitles:

        current = current.copy()

        if not result:
            result.append(current)
            continue

        previous = result[-1]

        if should_merge(
            previous,
            current,
        ):

            print(
                "合并字幕："
                f"「{previous['text']}」"
                f" + "
                f"「{current['text']}」"
            )

            previous["text"] = (
                previous["text"]
                + current["text"]
            )

            previous["end"] = max(
                previous["end"],
                current["end"],
            )

        else:
            result.append(current)

    return result

# ============================================================
# SRT 输出
# ============================================================

# ============================================================
# 折行
# ============================================================

LINE_MAX = 20


def wrap_lines(text, limit=LINE_MAX):
    """把过长的字幕折成两行。

    日语字幕单行超过 20 字就会顶到屏幕边缘，播放器自己折行的话
    往往断在很难看的位置（比如词中间、助词前后）。这里主动在标点
    或助词后面断开，并且保证两行长度不会太失衡。
    """

    if len(text) <= limit:
        return [text]

    middle = len(text) // 2

    # 每行至少占四分之一，避免折出「7 字 + 25 字」这种畸形
    min_line = max(5, len(text) // 4)
    upper = len(text) - min_line

    def pick(predicate):
        """在所有合法断点里，选离中点最近的那个。"""
        options = [
            pos for pos in range(min_line, upper + 1)
            if predicate(pos)
        ]
        return min(options, key=lambda pos: abs(pos - middle)) if options else None

    # 1. 标点后面（最自然）
    pos = pick(lambda p: text[p - 1] in "、。，．！？…")
    if pos is not None:
        return [text[:pos], text[pos:]]

    # 2. 助词后面（日语里最常见的语义边界）
    pos = pick(lambda p: text[p - 1] in "はがをにでとものもやへからまで")
    if pos is not None:
        return [text[:pos], text[pos:]]

    # 3. 兜底：对半开
    return [text[:middle], text[middle:]]


def write_srt(
    subtitles,
    output_file,
):

    with open(
        output_file,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        for index, subtitle in enumerate(
            subtitles,
            start=1,
        ):

            f.write(
                f"{index}\n"
            )

            f.write(
                f"{format_time(subtitle['start'])}"
                f" --> "
                f"{format_time(subtitle['end'])}\n"
            )

            for line in wrap_lines(subtitle["text"]):
                f.write(f"{line}\n")

            f.write("\n")


# ============================================================
# Main
# ============================================================

def main(video_name=None):

    global SRT_FILE

    if video_name:
        video_path = Path(video_name)

        SRT_FILE = (
            paths.TEMP_DIR
            / f"{video_path.stem}.ja.srt"
        )

    else:
        SRT_FILE = (
            paths.TEMP_DIR
            / "output.ja.srt"
        )

    print(
        f"读取 Manifest："
        f"{MANIFEST_FILE}"
    )

    with open(
        MANIFEST_FILE,
        "r",
        encoding="utf-8",
    ) as f:
        windows = json.load(f)

    print(
        f"读取到 {len(windows)} 个窗口"
    )
    # ========================================================
    # 1. Whisper Window -> Candidate
    # ========================================================

    candidates = []

    print(
        "\n=== Whisper 候选结果 ==="
    )

    for window in windows:

        json_file = (
            WHISPER_DIR
            / window["json"]
        )

        candidate = tokens_to_candidate(
            window,
            window,
        )

        if candidate is None:
            continue

        candidates.append(candidate)

        print(
            f"\n候选 {len(candidates):03d}"
        )

        print(
            f"Window："
            f"{candidate['window']:03d}"
        )

        print(
            f"时间："
            f"{format_time(candidate['start'])}"
            f" -> "
            f"{format_time(candidate['end'])}"
        )

        print(
            f"文本："
            f"{candidate['text']}"
        )

    # ========================================================
    # 2. Candidate 去重
        # ========================================================

    print(
        "\n=== 重复检测 ==="
    )

    duplicates = find_duplicates(
       candidates
    )

    candidates = [
        candidate
        for index, candidate in enumerate(candidates)
        if index not in duplicates
    ]

    print(
        "\n=== 去重后 ==="
    )

    for candidate in candidates:

        print(
            f"{format_time(candidate['start'])}"
            f" -> "
            f"{format_time(candidate['end'])}"
            f"    "
            f"{candidate['text']}"
        )

    # ========================================================
    # 3. Token -> 字幕
    # ========================================================

    print(
        "\n=== Token -> 字幕分组 ==="
    )

    subtitles = []

    for candidate in candidates:

        groups = split_tokens(
            candidate["tokens"],
            max_chars=14,
        )

        print(
            f"\nWindow "
            f"{candidate['window']:03d}"
        )

        for group in groups:

            subtitle = group_to_subtitle(group)
            if subtitle is None:
                continue

            subtitle["window"] = candidate["window"]
            
            subtitles.append(
                subtitle
            )

            print(
                f"{format_time(subtitle['start'])}"
                f" -> "
                f"{format_time(subtitle['end'])}"
            )

            print(
                f"  {subtitle['text']}"
            )

        # ========================================================
    # 4. 字幕候选去重
    # ========================================================

    print(
        "\n=== 字幕候选去重 ==="
    )

    subtitles = deduplicate_subtitles(
        subtitles
    )

    print(
        f"去重后字幕数量："
        f"{len(subtitles)}"
    )

    # ========================================================
    # 5. 断句合并
    # ========================================================

    print(
        "\n=== 断句合并 ==="
    )

    subtitles = merge_subtitles(
        subtitles
    )

    print(
        f"合并后字幕数量："
        f"{len(subtitles)}"
    )

    # ========================================================
    # 6. 时间优化
    # ========================================================

    print(
        "\n=== 时间优化 ==="
    )

    subtitles = drop_hallucinations(
        subtitles
    )

    subtitles = resolve_overlaps(
        subtitles
    )

    subtitles = optimize_timing(
        subtitles
    )

    for subtitle in subtitles:
        print(
            f"{format_time(subtitle['start'])}"
            f" -> "
            f"{format_time(subtitle['end'])}"
            f"    "
            f"{subtitle['text']}"
        )

    # ========================================================
    # 7. SRT
    # ========================================================

    print(
        "\n=== 写入 SRT ==="
    )

    write_srt(
        subtitles,
        SRT_FILE,
    )

    print(
        f"SRT：{SRT_FILE}"
    )

    print(
        f"字幕数量：{len(subtitles)}"
    )



if __name__ == "__main__":

    video_name = (
        sys.argv[1]
        if len(sys.argv) > 1
        else None
    )

    main(video_name)