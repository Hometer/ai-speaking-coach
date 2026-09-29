"""语音转写识别风险判定（REQ-32，确定性，不新增模型调用）。

风险等级只表示「识别可能不可靠」，不是识别准确率、不是能力分数；
界面不得据此声称用户说错。规则要豁免简短有效回答（单词少本身不是错误）。
"""
import re
from typing import Any

# 简短有效回答豁免：单词少本身不是错误，不能仅因字数少拦截
_SHORT_EXEMPT = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "water",
    "maybe", "thanks", "thank you", "please", "yes please", "no thanks",
    "hi", "hello", "fine", "good", "great",
}

# 疑似句尾截断的结尾词（说话被切断时常停在这些词上）
_TRUNCATED_TAIL = {
    "and", "the", "a", "an", "to", "of", "in", "on", "at", "for", "but",
    "because", "so", "or", "with", "about", "from", "that", "my", "is",
}


def assess_risk(transcript: str) -> tuple[str, list[str]]:
    """返回 (level, reason_codes)，level ∈ low/mid/high。"""
    text = (transcript or "").strip()
    reasons: list[str] = []

    if not text:
        return "high", ["empty"]

    lowered = " ".join(text.lower().split())

    # 简短有效回答豁免
    if lowered in _SHORT_EXEMPT:
        return "low", []

    # 乱码/噪声：非字母数字空白占比过高
    non_lang = len(re.findall(r"[^a-zA-Z0-9\s'.,?!-]", text))
    total = len(text)
    if total > 0 and non_lang / total > 0.3:
        return "high", ["garbage"]

    # 异常重复：同一词连续 ≥3 次
    words = lowered.split()
    for i in range(len(words) - 2):
        if words[i] == words[i + 1] == words[i + 2]:
            reasons.append("repeated")
            break

    # 疑似句尾截断
    if words and words[-1] in _TRUNCATED_TAIL:
        reasons.append("truncated")

    if "repeated" in reasons or "truncated" in reasons:
        return "mid", reasons
    return "low", []


def risk_record_summary(level: str, reasons: list[str]) -> str:
    return ";".join(reasons) if reasons else ""
