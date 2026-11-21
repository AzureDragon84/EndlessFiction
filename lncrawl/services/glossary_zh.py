"""Chinese candidate extraction: segmentation, PMI/entropy repair, filters.

The scanner this replaces cut fixed 2–6 character windows out of every Han run,
so it had no notion of a word: `艘星` (half of `星舰`), `经` (the tail of
`九转金经`) and `恭喜获` (a system message) were all candidates, and the only
thing standing between them and a reviewer was a stop-character list. Here the
text is segmented with jieba's POS tagger, so a fragment cannot be born, and the
one thing jieba gets wrong on web novels — splitting a compound name or a hull
class into several words — is repaired with PMI and branch entropy.

Everything is per batch: PMI and entropy are corpus statistics, so the caller
hands over every chapter of the scan at once. `jieba` is imported lazily and
its absence degrades to the old n-gram windows with a warning, which keeps the
CLI and test installs working without a 20 MB dictionary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import logging
import math
import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple
import unicodedata

from .glossary_profile import Batch

logger = logging.getLogger(__name__)

# POS tags worth a reviewer's attention: proper nouns of every kind plus the
# common-noun tags that carry items, techniques and titles.
CANDIDATE_FLAGS = frozenset({"nr", "ns", "nt", "nz", "n", "nl", "j"})
# Proper nouns are trusted on their own; a common noun has to earn its place.
NAME_FLAGS = frozenset({"nr", "ns", "nt", "nz", "j"})
# A jieba dictionary word this common is vocabulary, not this novel's term.
# It is the free stand-in for a wordfreq keyness pass (plan §4 stage 5).
COMMON_WORD_FREQ = 200
# A surface longer than this is a sentence fragment, not a name.
MAX_SURFACE = 12
# Pointwise mutual information floor for fusing two adjacent tokens.
PMI_MIN = 2.5
# A fused string needs at least this many bits of boundary entropy to be a
# unit rather than one more fragment of a longer phrase.
ENTROPY_MIN = 0.5
ENTROPY_KEEP = 0.9
# `中型星际` + `驱逐舰` takes a second round; three cover every hull class
# the corpus produced.
FUSE_ROUNDS = 3

_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_RUN = re.compile(f"[{_HAN}]+")
_HAN_ONLY = re.compile(f"[{_HAN}]+")
# Ruby annotations (`｜漢字《かな》`) are reading aids, not story text.
_RUBY = re.compile(f"[{_HAN}]+《[^》]*》")

# Leading classifiers, numerals and trailing particles that survive
# segmentation when a name sits next to a measure word (`三艘星舰`, `林凡的`).
_LEADING = frozenset("艘型款些件个名位只把条张枚颗座柄架台匹头一二三四五六七八九十百千万两")
# Only real particles are stripped or rejected. An earlier set included 太 and
# 之, which turned `灭绝师太` into `灭绝师` and dropped
# `北之星星舰军事学院` outright.
_TRAILING = frozenset("的了着过吗呢吧啊呀哦")
# Grammar that means "these characters are a phrase, not a name".
_GRAMMAR = frozenset("的了着过往是在有和与也都就还很我你他她它们这那哪谁什么怎吗呢吧啊呀哦嘛")
# Titles that make a common-noun token a name to review: `灭绝师太` is tagged
# `n`, but it is the only way chapter one names that character. Also the list a
# `zh` profile hands the paid stages as its honorifics.
HONORIFICS = _TITLES = (
    "师太",
    "道长",
    "真人",
    "仙子",
    "圣女",
    "少爷",
    "小姐",
    "姑娘",
    "大人",
    "长老",
    "族长",
    "领主",
    "总督",
    "将军",
    "阁下",
    "殿下",
    "陛下",
    "先生",
    "夫人",
)
# Words a name may fuse with: organisations, places and hull classes. Without
# this, `学员` + `赵辰` and `舰桥` + `夏洛特` became "terms".
_SUFFIXES = frozenset(
    "帝国 联邦 共和国 王国 公国 学院 大学 联盟 公会 商会 舰队 集团 财团 公司 军团 军 "
    "家族 家 族 城 宗 门 派 殿 阁 盟 堂 会 星系 星域 星港 星区 星球 基地 港 站 "
    "级 型 号 舰 船 王 皇 女王 大人 总督 领主 城主 将军 族长 长老 殿下 陛下 商城 系".split()
)

# Common vocabulary that a novel-level glossary never wants, whatever the
# dictionary thinks: pronouns, light nouns, narrative filler and game-system
# boilerplate.
STOP_WORDS = frozenset(
    """
    自己 我们 他们 她们 你们 咱们 什么 怎么 为什么 怎么样 这样 那样 这里 那里
    这个 那个 这些 那些 这种 那种 其中 之间 之后 之前 现在 当时 已经 还是 就是
    但是 而且 因为 所以 如果 虽然 然后 于是 因此 可以 可能 应该 需要 不能 不会
    不用 没有 不是 一样 一起 一直 一定 一点 一些 一次 一天 一边 一下 一般 一切
    所有 任何 每个 另一 其他 别的 如此 多少 几个 许多 很多 不少 大家 别人 人家
    本人 对方 双方 众人 人们 有人 没人 时候 时间 地方 东西 事情 问题 办法 方法
    感觉 声音 目光 眼神 脸色 表情 心中 心里 身上 手中 眼中 眼前 周围 身边 面前
    身后 头上 脚下 空中 天空 地面 世界 天地 天下 空间 力量 气息 灵气 能量 速度
    方向 位置 距离 样子 模样 形状 颜色 光芒 光线 气氛 场面 情况 状态 结果 原因
    过程 机会 条件 关系 意思 想法 念头 计划 行动 动作 反应 变化 影响 作用 效果
    目的 目标 任务 奖励 惩罚 系统 提示 消息 通知 话语 觉得 知道 看到 听到 发现
    认为 以为 明白 理解 记得 忘记 想要 希望 喜欢 讨厌 害怕 担心 相信 怀疑 决定
    选择 开始 结束 继续 停止 离开 回来 回去 出来 进来 上来 下来 过来 过去 起来
    出现 消失 发生 成为 变成 保持 坚持 使用 利用 得到 获得 失去 找到 看见 听见
    说话 回答 问道 说道 笑道 点头 摇头 转身 抬头 低头 皱眉 冷哼 沉默 深吸 微微
    轻轻 缓缓 慢慢 忽然 突然 立刻 马上 顿时 终于 果然 竟然 居然 当然 自然 依然
    仍然 始终 正在 刚刚 曾经 从来 向来 十分 非常 特别 相当 极其 极为 无比 更加
    越来越 渐渐 逐渐 猛地 猛然 狠狠 重重 紧紧 悄悄 默默 静静 淡淡 冷冷 微笑 苦笑
    修炼 修炼者 修士 武者 强者 高手 大人 前辈 兄弟 朋友 敌人 对手 男人 女人 少年
    少女 老者 青年 中年人 孩子 家伙 所有人 族长 宗主 长老 弟子 师兄 师姐 师弟
    师妹 掌柜 老板 皇帝 陛下 殿下 公子 小姐 姑娘 夫人 名字 姓氏 恭喜 领取 兑换
    选项 宿主 签到 叮咚 提示音 任务完成 奖励发放 是否 确认 取消 完毕 退学 看向
    起身 站起 坐起 说道 想著 看着 听着 说着 知道 要是 一个人 两个 三个 决斗 休眠
    规避 试航 傻眼 长椅 白眼 马尾 嘴角 眼神 餐食 标准 强度 幅度 频率 数量 质量
    """.split()
)

# Suffixes that turn a proper noun into an organisation, place or hull class.
# A pattern match is kept at a lower bar than a bare common noun because the
# suffix is evidence on its own; `_GRAMMAR` keeps phrases out.
_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(f"[{_HAN}]{{2,8}}(?:帝国|联邦|共和国|学院|联盟|公会|商会|舰队)"),
    re.compile(f"[{_HAN}]{{2,5}}(?:级|型|号)(?:[{_HAN}]{{0,2}}(?:舰|船|飞船|战列舰|驱逐舰))?"),
    re.compile(f"[{_HAN}]{{2,4}}(?:宗|门|派|殿|阁|盟|教|堂|会)"),
    re.compile(f"老[{_HAN}]{{1,2}}(?![{_HAN}])"),
    re.compile(f"[{_HAN}]{{2,3}}总(?![{_HAN}])"),
)
# A surname immediately before a dialogue verb is a speech tag: `林凡说道`.
_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻"
    "柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤"
    "滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵汪祁毛禹狄米"
    "贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭"
    "梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘解应宗丁宣邓郁单杭洪"
    "包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲封芮储靳汲邴松井段富巫乌焦巴弓牧隗"
    "山谷车侯全班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶黎薄印宿白"
    "怀蒲邰从鄂索咸赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳姬申扶堵冉宰郦雍桑"
    "桂濮牛寿通边扈燕冀浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾"
    "终暨居衡步耿满弘匡国文寇广禄阙东欧沃利蔚越隆师巩聂晁勾融冷訾辛阚简饶曾"
    "沙养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
_DIALOGUE = (
    "说道|问道|笑道|答道|喝道|叹道|开口|点头|冷哼|皱眉|沉声|低声|轻声"
    "|微笑道|冷笑道|淡淡道|大笑道|低声道|轻声道"
)
# The given name is matched lazily so `赵辰微笑道` yields `赵辰`, not `赵辰微`.
_SPEECH_TAG = re.compile(f"([{_SURNAMES}])([{_HAN}]{{1,2}}?)(?:{_DIALOGUE})")

_jieba_ready: bool | None = None


def normalize(text: str) -> str:
    """NFKC, full-width folding and ruby removal before any token work."""
    body = unicodedata.normalize("NFKC", str(text or ""))
    return _RUBY.sub("", body)


def available() -> bool:
    """Whether jieba can be imported, decided once per process."""
    global _jieba_ready
    if _jieba_ready is None:
        try:
            import jieba
            import jieba.posseg  # noqa: F401

            # Load the dictionary up front: `_common` reads `dt.FREQ`, and a
            # lazy load would make the first scan judge words differently from
            # the next one.
            jieba.initialize()
        except ImportError:
            logger.warning("jieba is not installed; falling back to n-gram windows")
            _jieba_ready = False
        else:
            _jieba_ready = True
    return _jieba_ready


def collect(pages: Iterable[Tuple[int, str]]) -> Batch:
    """Segment a batch and return its per-chapter candidate counts.

    Each fusion round is one corpus pass: count tokens and adjacent pairs, keep
    the pairs whose PMI and fused boundary entropy say "one unit", then merge
    them. Repeating the round is what turns `中型` + `星际` + `驱逐舰` into the
    hull class it is instead of three fragments.
    """
    texts = [(serial, normalize(text)) for serial, text in pages]
    streams = [(serial, _tokens(text)) for serial, text in texts]
    for _ in range(FUSE_ROUNDS):
        streams, changed = _fuse_round(streams)
        if not changed:
            break

    batch = Batch()
    for (serial, text), (_, tokens) in zip(texts, streams):
        counts: Counter = Counter()
        for surface, flag in tokens:
            surface = _trim(surface)
            if flag not in CANDIDATE_FLAGS or not _keep(surface):
                continue
            counts[surface] += 1
            if flag in NAME_FLAGS or surface.endswith(_TITLES):
                batch.names.add(surface)
        for surface, count in _pattern_candidates(text).items():
            if not _keep(surface):
                continue
            counts[surface] = max(counts.get(surface, 0), count)
            batch.names.add(surface)
        batch.counts[serial] = dict(counts)
    return batch


def _tokens(text: str) -> List[Tuple[str, str]]:
    """(surface, POS) pairs for the Han tokens of one chapter."""
    if not available():
        return _fallback_tokens(text)
    import jieba.posseg as pseg

    tokens: List[Tuple[str, str]] = []
    for pair in pseg.cut(text):
        word = pair.word.strip()
        if not word or not _HAN_ONLY.fullmatch(word):
            continue
        if len(word) > MAX_SURFACE:
            continue
        tokens.append((word, pair.flag))
    return tokens


def _fallback_tokens(text: str) -> List[Tuple[str, str]]:
    """Old-window stand-in: 2–6 character runs, tagged as common nouns.

    Without a segmenter every window is a candidate, which is exactly the
    fragmentation this phase removes; it stays only so a missing optional
    dependency degrades instead of crashing a scan.
    """
    tokens: List[Tuple[str, str]] = []
    for run in _HAN_RUN.findall(text):
        tokens.append((run, "n"))
        for size in range(2, min(6, len(run)) + 1):
            tokens.extend((run[start : start + size], "n") for start in range(len(run) - size + 1))
    return tokens


def _fuse_round(
    streams: Sequence[Tuple[int, List[Tuple[str, str]]]],
) -> Tuple[List[Tuple[int, List[Tuple[str, str]]]], bool]:
    """One PMI/entropy fusion pass over the current token streams."""
    unigrams: Counter = Counter()
    bigrams: Counter = Counter()
    flags: Dict[str, str] = {}
    for _, tokens in streams:
        for word, flag in tokens:
            unigrams[word] += 1
            flags.setdefault(word, flag)
        bigrams.update((tokens[i][0], tokens[i + 1][0]) for i in range(len(tokens) - 1))

    total = sum(unigrams.values()) or 1
    candidates: Dict[Tuple[str, str], float] = {}
    for pair, count in bigrams.items():
        left, right = pair
        if count < 2 or len(left) + len(right) > MAX_SURFACE:
            continue
        if unigrams[left] < 2 or unigrams[right] < 2:
            continue
        if flags.get(left) not in CANDIDATE_FLAGS or flags.get(right) not in CANDIDATE_FLAGS:
            continue
        # A name next to an ordinary noun is prose (`学员` + `赵辰`) unless the
        # noun is a suffix that makes a place, organisation or hull class.
        left_name = flags.get(left) in NAME_FLAGS
        right_name = flags.get(right) in NAME_FLAGS
        if left_name and not right_name and right not in _SUFFIXES:
            continue
        if right_name and not left_name and left not in _SUFFIXES:
            continue
        if not _keep(_trim(left + right)):
            continue
        pmi = math.log2(count * total / (unigrams[left] * unigrams[right]))
        if pmi < PMI_MIN:
            continue
        candidates[pair] = pmi
    if not candidates:
        return list(streams), False

    left_ctx: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    right_ctx: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    word_ctx: Dict[str, Counter] = defaultdict(Counter)
    for _, tokens in streams:
        for index in range(len(tokens) - 1):
            pair = (tokens[index][0], tokens[index + 1][0])
            if pair not in candidates:
                continue
            if index:
                left = tokens[index - 1][0]
                left_ctx[pair][left] += 1
                word_ctx[_side(tokens[index][0], "l")][left] += 1
            if index + 2 < len(tokens):
                right = tokens[index + 2][0]
                right_ctx[pair][right] += 1
                word_ctx[_side(tokens[index + 1][0], "r")][right] += 1

    accepted: Set[Tuple[str, str]] = set()
    for pair in candidates:
        entropy = _entropy(left_ctx[pair]) + _entropy(right_ctx[pair])
        own = [
            _entropy(word_ctx[key])
            for key in (_side(pair[0], "l"), _side(pair[1], "r"))
            if key in word_ctx
        ]
        floor = min(own) if own else ENTROPY_MIN
        if entropy >= ENTROPY_MIN and entropy >= floor * ENTROPY_KEEP:
            accepted.add(pair)
    if not accepted:
        return list(streams), False

    fused: List[Tuple[int, List[Tuple[str, str]]]] = []
    for serial, tokens in streams:
        fused.append((serial, _fuse(tokens, accepted)))
    return fused, True


def _fuse(tokens: Sequence[Tuple[str, str]], pairs: Set[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Merge accepted pairs left to right, keeping the stronger POS tag."""
    merged: List[Tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        word, flag = tokens[index]
        if index + 1 < len(tokens) and (word, tokens[index + 1][0]) in pairs:
            other, other_flag = tokens[index + 1]
            word = _trim(word + other)
            if flag not in NAME_FLAGS and other_flag in NAME_FLAGS:
                flag = other_flag
            index += 2
        else:
            index += 1
        merged.append((word, flag))
    return merged


def _side(word: str, side: str) -> str:
    """Key for one side of a token's neighbours (tokens can be both sides)."""
    return f"{side}:{word}"


def _trim(surface: str) -> str:
    start, end = 0, len(surface)
    while end - start > 2 and surface[start] in _LEADING:
        start += 1
    while end - start > 2 and surface[end - 1] in _TRAILING:
        end -= 1
    return surface[start:end]


def _keep(surface: str) -> bool:
    """Stage 4: the cheap rules that no statistic has to see."""
    text = surface.strip()
    if len(text) < 2 or len(text) > MAX_SURFACE:
        return False
    if not _HAN_ONLY.fullmatch(text):
        return False
    if text in STOP_WORDS:
        return False
    if any(char in _TRAILING or char in _GRAMMAR for char in text):
        return False
    # `警告警告警告` is one word shouted, not three occurrences of a name.
    if any(
        text == text[:size] * (len(text) // size) and len(text) % size == 0
        for size in range(2, len(text) // 2 + 1)
    ):
        return False
    # `三艘`, `百名`, `个月`: a numeral or classifier that segmentation kept
    # as its own word is a measure phrase, never a term.
    if text[0] in _LEADING and len(text) <= 3:
        return False
    if re.fullmatch("[一二三四五六七八九十百千万两零]+", text):
        return False
    # A title-carrying name is a name even when jieba's dictionary knows the
    # words (`灭绝师太` is vocabulary to jieba, a character to this novel).
    if len(text) >= 3 and text.endswith(_TITLES):
        return True
    if _common(text):
        return False
    return True


def _common(surface: str) -> bool:
    """True when jieba's own dictionary says this is ordinary vocabulary."""
    if not available():
        return False
    import jieba

    return int(jieba.dt.FREQ.get(surface, 0)) >= COMMON_WORD_FREQ


def _pattern_candidates(text: str) -> Counter:
    """Surfaces the suffix patterns and speech tags find on their own."""
    found: Counter = Counter()
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            surface = match.group(0)
            if _GRAMMAR.isdisjoint(surface):
                found[surface] += 1
    for match in _SPEECH_TAG.finditer(text):
        surface = match.group(1) + match.group(2)
        if _GRAMMAR.isdisjoint(surface):
            found[surface] += 1
    return found


def _entropy(counter: Counter) -> float:
    total = sum(counter.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counter.values())
