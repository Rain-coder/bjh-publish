from bjh.fetcher.baijiahao import parse_baidu_serp, parse_article_stats

SERP = """
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=1">月薪三万却不敢生孩子的年轻人_百家号</a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://www.example.com/news/1"> unrelated 普通网站文章 </a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=2">职场焦虑真相大揭秘_百家号</a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=1">月薪三万却不敢生孩子的年轻人_百家号</a></h3>
</div>
"""

ARTICLE = """
<script>window._show = {"readCount": "12345"}</script>
<div>评论 234</div>
"""


def test_parse_baidu_serp_filters_and_dedupes():
    out = parse_baidu_serp(SERP, "月薪")
    urls = [t.url for t in out]
    assert urls == ["https://baijiahao.baidu.com/s?id=1", "https://baijiahao.baidu.com/s?id=2"]
    assert out[0].title == "月薪三万却不敢生孩子的年轻人"


def test_parse_article_stats_multi_pattern():
    stats = parse_article_stats(ARTICLE)
    assert stats == {"reads": 12345, "comments": 234}
    assert parse_article_stats('{"read_count": 99, "commentCount": 8}') == {"reads": 99, "comments": 8}
    assert parse_article_stats("什么都没有") == {"reads": 0, "comments": 0}
