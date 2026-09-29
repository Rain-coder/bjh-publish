from bjh.models import Topic
from bjh.writer.topic import heat_score, title_bigrams, jaccard, dedupe_topics, select_topics


def T(title, reads, comments, heat=1.0):
    return Topic(keyword="kw", title=title, url="u", source_author="a",
                 reads=reads, comments=comments, heat_score=heat,
                 fetched_at="2026-09-29T10:00:00")


def test_heat_score_weights_comments():
    # 同阅读量下，评论多者显著更高；公式 = ln(reads+1) + 2.2*ln(comments+1)
    import math
    assert heat_score(10000, 10) == math.log(10001) + 2.2 * math.log(11)
    assert heat_score(10000, 500) > heat_score(10000, 10) + 3


def test_bigrams_and_jaccard():
    a = title_bigrams("月薪三万不敢生孩子")
    assert "月薪" in a and len(a) >= 6
    assert jaccard(title_bigrams("月薪三万不敢生孩子"), title_bigrams("月薪三万不敢生孩子")) == 1.0
    assert jaccard(title_bigrams("月薪三万"), title_bigrams("周末去哪玩")) == 0.0


def test_dedupe_keeps_higher_heat():
    t1 = T("月薪三万不敢生孩子的年轻人", 1000, 10, heat=5.0)
    t2 = T("月薪三万不敢生孩子？年轻人这样想", 1000, 10, heat=4.0)  # 相似标题
    t3 = T("完全不同的话题标题", 500, 5, heat=1.0)
    out = dedupe_topics([t2, t1, t3])           # t1 在前应保留 t1 去掉 t2
    titles = [t.title for t in out]
    assert "完全不同的话题标题" in titles
    assert titles.count("月薪三万不敢生孩子的年轻人") == 1
    dup = [t for t in out if "不敢生孩子" in t.title]
    assert len(dup) == 1 and dup[0].heat_score == 5.0


def test_select_topics_excludes_used():
    used = ["已经做过的标题"]
    t1 = T("已经做过的标题", 100, 900, heat=9.0)
    t2 = T("新选题标题", 100, 800, heat=8.0)
    out = select_topics([t1, t2], n=1, used_titles=used)
    assert [t.title for t in out] == ["新选题标题"]
