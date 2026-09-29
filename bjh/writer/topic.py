# bjh/writer/topic.py
import math
from bjh.models import Topic


def heat_score(reads: int, comments: int) -> float:
    """评论权重 2.2 倍 —— 对齐"评论区互动"目标。"""
    return math.log(reads + 1) + 2.2 * math.log(comments + 1)


def title_bigrams(title: str) -> set[str]:
    s = "".join(c for c in title if c.isalnum())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe_topics(topics: list[Topic], threshold: float = 0.55) -> list[Topic]:
    """按热度降序遍历，与已保留标题相似度 >= threshold 的丢弃。"""
    kept: list[Topic] = []
    for t in sorted(topics, key=lambda x: x.heat_score, reverse=True):
        bi = title_bigrams(t.title)
        if any(jaccard(bi, title_bigrams(k.title)) >= threshold for k in kept):
            continue
        kept.append(t)
    return kept


def select_topics(topics: list[Topic], n: int = 1,
                  used_titles: list[str] | None = None) -> list[Topic]:
    used_titles = used_titles or []
    fresh = [t for t in topics if t.title not in used_titles]
    return dedupe_topics(fresh)[:n]
