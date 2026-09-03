"""检索 query 的轻量中文处理（V0 启发式）。

说明：FTS5 unicode61 对 CJK 按单字分 token，直接 MATCH 长 query 会被
“全部单字 AND”卡死。这里把 query 清洗后拆成去重二元组，用 OR 连接，
保证召回；排序交给 bm25。属于 V0 妥协，后续可被 Query Analyzer / rewrite 替换。
"""
from __future__ import annotations

# 常见虚词/语气字，逐个剔除（注意：不剔除 不/对/前/反 等可能参与实义的字符）
_STOP_CHARS = (
    "的了么吗呢啊吧嘛呀哦嗯唉咦之与及和或但而却并且也又再就都只还很最更"
    "被把让从向给在是有这那要会能可以应该怎什为哪谁何哪些这个"
)


def alnum_chars(query: str) -> list[str]:
    """只保留字母数字与 CJK，去掉标点/空白。"""
    return [c for c in query if c.isalnum()]


def content_chars(query: str) -> list[str]:
    """去掉虚词后的有效字；若删空则退回原始字符，避免查询完全失效。"""
    chars = alnum_chars(query)
    keep = [c for c in chars if c not in _STOP_CHARS]
    return keep if len(keep) >= 2 else chars


def unique_bigrams(chars: list[str]) -> list[str]:
    """有序去重二元组。"""
    seen: set[str] = set()
    out: list[str] = []
    for i in range(len(chars) - 1):
        b = chars[i] + chars[i + 1]
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def fts_expr(query: str) -> str:
    """把自然语言 query 转成 FTS5 OR-短语表达式；空 query 返回空串。"""
    chars = content_chars(query)
    if not chars:
        return ""
    bigrams = unique_bigrams(chars)
    if bigrams:
        return " OR ".join(f'"{b}"' for b in bigrams)
    return " OR ".join(f'"{c}"' for c in sorted(set(chars)))
