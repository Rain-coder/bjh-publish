import json
from datetime import datetime
import pytest
from bjh.config import Config
from bjh.db import DB
from bjh.models import Topic
from bjh.writer.service import write_draft

T = Topic(keyword="职场", title="月薪三万不敢生孩子", url="u", source_author="a",
          reads=100000, comments=2300, heat_score=1.0, fetched_at="2026-09-29T10:00:00")

# 口播稿必须落在 150~320 字（parse_llm_json 校验）：177 字
GOOD = {"titles": ["月薪三万，为什么不敢生孩子？", "候选二标题", "候选三标题"],
        "title": "月薪三万，为什么不敢生孩子？", "hook": "你敢信吗",
        "script": ("月薪三万。" + "听起来很多的都市白领们。每一个月都在精打细算着过日子。" * 6
                   + "但真的够吗？" + "你怎么看？"),
        "closing_question": "你觉得够不够？", "tags": ["职场", "收入"]}
# 含敏感词"震惊"且字数合法的脏样本（187 字）
BAD = dict(GOOD, title="震惊！月薪三万", script=GOOD["script"] + "这个消息很震惊。")


class FakeClient:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = 0

    def chat(self, messages, json_mode=False, retries=2):
        self.calls += 1
        return self.contents[min(self.calls - 1, len(self.contents) - 1)], \
            {"prompt_tokens": 1000, "completion_tokens": 500}


def test_write_draft_ok(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(GOOD, ensure_ascii=False)))
    assert d.status == "ok" and d.title == GOOD["title"] and d.tags == ["职场", "收入"]
    assert d.cost > 0 and db.get_draft(d.id).hook == "你敢信吗"


def test_write_draft_sensitive_retry_then_blocked(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(BAD, ensure_ascii=False),
                                                       json.dumps(BAD, ensure_ascii=False)))
    assert d.status == "blocked"            # 重写一次仍含"震惊" → blocked


def test_write_draft_retry_recovers(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(BAD, ensure_ascii=False),
                                                       json.dumps(GOOD, ensure_ascii=False)))
    assert d.status == "ok"                 # 第二次干净 → 通过


def test_write_draft_needs_key_without_client(tmp_path):
    db = DB(tmp_path / "t.db")
    with pytest.raises(ValueError, match="deepseek_api_key"):
        write_draft(db, Config(), T)


def test_image_queries_returns_per_segment_queries():
    from bjh.writer.service import image_queries
    qs = ["office salary", "city night", "worried parents",
          "calculator budget", "crying baby", "family walk"]
    c = FakeClient(json.dumps({"queries": qs}, ensure_ascii=False))
    assert image_queries(c, GOOD["script"], 6) == qs


def test_image_queries_cycles_when_fewer_than_n():
    from bjh.writer.service import image_queries
    c = FakeClient(json.dumps({"queries": ["office salary", "city night"]}, ensure_ascii=False))
    out = image_queries(c, GOOD["script"], 6)
    assert out == ["office salary", "city night", "office salary", "city night",
                   "office salary", "city night"]


def test_image_queries_returns_none_on_bad_output():
    from bjh.writer.service import image_queries
    assert image_queries(FakeClient("不是json"), GOOD["script"], 6) is None
    assert image_queries(FakeClient(json.dumps({"queries": []})), GOOD["script"], 6) is None


def test_image_queries_rejects_cjk_then_retries_translation():
    """Pexels 只认英文：模型回中文时剔除并要求翻译重试；仍中文才放弃。"""
    from bjh.writer.service import image_queries
    zh = json.dumps({"queries": ["年轻人焦虑", "办公室加班"]}, ensure_ascii=False)
    still_zh = json.dumps({"queries": ["还是中文"]}, ensure_ascii=False)
    c = FakeClient(zh, still_zh)
    assert image_queries(c, GOOD["script"], 4) is None
    assert c.calls == 2                                       # 重试过一次
    en = json.dumps({"queries": ["young man anxious", "office overtime"]})
    ok = FakeClient(zh, en)
    assert image_queries(ok, GOOD["script"], 4) == \
        ["young man anxious", "office overtime", "young man anxious", "office overtime"]
