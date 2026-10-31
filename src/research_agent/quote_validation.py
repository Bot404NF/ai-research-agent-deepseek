"""核对直接引文原字，不推断语义支持、不自动校改OCR。"""

import re
import unicodedata


def _normalize(text):
    return "".join(
        c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def _matches(quote, text):
    # 明确省略号允许跳过原文；不能把多个来源拼成一段引文。
    parts = [_normalize(p) for p in re.split(r"…+|\.{3,}", quote)]
    text = _normalize(text)
    offset = 0
    for part in filter(None, parts):
        position = text.find(part, offset)
        if position < 0:
            return False
        offset = position + len(part)
    return bool(any(parts))


def check_direct_quotes(answer, evidence):
    """只检查有引文语境的引号/引用块；短术语和语义正确性不在此函数判定。"""
    sources = {e["label"]: e["text"] for e in evidence}
    issues, checked = [], 0
    blocks = re.split(r"\n\s*\n", answer)
    for block in blocks:
        quotes = []
        for match in re.finditer(r"“([^”\n]+)”|「([^」\n]+)」", block):
            before = block[max(0, match.start() - 50) : match.start()]
            quote = match.group(1) or match.group(2)
            if len(_normalize(quote)) >= 8 or re.search(r"原文|引文|著录|记[载为]|写道", before):
                quotes.append(quote)
        if not quotes and block.lstrip().startswith(">"):
            quotes = [re.sub(r"\[[SNOW]\d+\]", "", re.sub(r"(?m)^>\s*", "", block)).strip()]
        labels = re.findall(r"\[([SNOW]\d+)\]", block)
        # 引用块常在下一段附标签，只允许使用相邻段提供的明确标签。
        if not labels and quotes:
            position = answer.find(block) + len(block)
            labels = re.findall(
                r"\[([SNOW]\d+)\]", answer[position : position + 120].split("\n\n", 2)[-1]
            )
        for quote in quotes:
            checked += 1
            if not any(label in sources and _matches(quote, sources[label]) for label in labels):
                issues.append(
                    {
                        "kind": "direct_quote_mismatch",
                        "labels": labels,
                        "quote": quote,
                        "reason": "直接引文未在对应标签的证据中逐字匹配。",
                    }
                )
    return {"checked": checked, "issues": issues}


def quote_notice(answer, evidence):
    result = check_direct_quotes(answer, evidence)
    if result["issues"]:
        answer += f"\n\n引文校验提示：{len(result['issues'])}处直接引文未能与所引证据逐字匹配，请核对原文。"
    return answer
