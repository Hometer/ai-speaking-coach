"""口语复述/脱稿/换题的确定性判定（AC10-01/02/03）。

四条同时决定能否升级"口语独立/迁移/掌握"：
1. 输入模态：必须 voice，text 只能记「文字完成」；
2. 帮助证据：服务端 help_shown 为真则记 hinted；
3. 照抄：与目标句逐字一致不算独立；
4. 语义相关：与目标句共享内容词达到阈值才判定，无法判断记 pending（不升级）。

确定性规则只做保守代理；真实语义由评估器/人工样例校准。
"""
import re

_STOP = {
    "a", "an", "the", "to", "in", "on", "at", "for", "of", "and", "or", "so",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her",
    "is", "are", "am", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "should", "that", "this", "what", "which", "with", "about", "from", "just",
}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9\s']", " ", (text or "").lower())


def content_words(text: str) -> set[str]:
    return {w for w in norm(text).split() if w and w not in _STOP}


def judge_recall(answer: str, target: str, input_type: str, help_shown: bool) -> str:
    """返回 independent / text_completed / hinted / failed / pending。

    注：不用「照抄原句」拦独立——答案隐藏时逐字回忆是合法成功；
    看答案后照抄由 help_shown→hinted 拦截。
    """
    a = (answer or "").strip()
    if len(norm(a).split()) < 2:
        return "failed"
    if help_shown:
        return "hinted"
    if input_type != "voice":
        return "text_completed"
    t_words = content_words(target or "")
    if not t_words:
        return "pending"
    a_words = content_words(a)
    overlap = len(a_words & t_words) / len(t_words)
    if overlap >= 0.4:
        return "independent"
    return "pending"


def judge_transfer(
    answer: str,
    original: str,
    input_type: str,
    help_shown: bool,
    reference_expressions: list[str] | None = None,
) -> str:
    """换题判定：不拿原句当标准，要求语音、非照抄原句、非过短，且与参考表达有 ≥2 个内容词重叠；否则无法判断记 pending。"""
    a = (answer or "").strip()
    if len(norm(a).split()) < 3:
        return "failed"
    if help_shown:
        return "hinted"
    if input_type != "voice":
        return "text_completed"
    if norm(original or "") and norm(a) == norm(original):
        return "failed"  # 复述原句≠回答新条件
    # 场景相关性：与参考表达的内容词重叠 ≥2 才可判为迁移，否则无法判断
    if reference_expressions:
        kw_set: set[str] = set()
        for kw in reference_expressions:
            for w in norm(kw).split():
                if w and w not in _STOP:
                    kw_set.add(w)
        a_words = content_words(a)
        if kw_set and len(a_words & kw_set) < 2:
            return "pending"
    return "transfer"
