from bjh.models import Topic
from bjh.writer.prompts import extract_rules, build_system_prompt, build_user_prompt, parse_llm_json, pick_title

SKILL = "前文\n<rules>\n规则A\n规则B\n</rules>\n后文"
T = Topic(keyword="职场", title="月薪三万不敢生孩子", url="u", source_author="a",
          reads=100000, comments=2300, heat_score=1.0, fetched_at="2026-09-29T10:00:00")


def test_extract_rules():
    assert "规则A" in extract_rules(SKILL) and "后文" not in extract_rules(SKILL)


def test_build_prompts_contain_rules_and_data():
    sp = build_system_prompt(extract_rules(SKILL))
    assert "规则A" in sp and "JSON" in sp
    up = build_user_prompt(T)
    assert "月薪三万不敢生孩子" in up and "100000" in up and "2300" in up


def test_parse_llm_json_strips_fence_and_validates():
    import json as _json
    good = '```json\n' + _json.dumps({"titles": ["a？", "b", "c"], "title": "a？", "hook": "h",
                                       "script": "口" * 200, "closing_question": "q", "tags": ["t"]}) + '\n```'
    d = parse_llm_json(good)
    assert d["title"] == "a？" and len(d["tags"]) == 1


def test_parse_llm_json_rejects_bad():
    import json
    import pytest
    with pytest.raises(ValueError):
        parse_llm_json('{"titles": ["只有标题"]}')          # 缺字段
    with pytest.raises(ValueError):
        parse_llm_json('```json\n' + json.dumps({"titles": ["a"] * 3, "title": "a", "hook": "h", "script": "太短", "closing_question": "q", "tags": []}) + '\n```')


def test_pick_title_prefers_question():
    assert pick_title(["数字式标题", "悬念式标题？", "代入式标题"]) == "悬念式标题？"
    assert pick_title(["只有一个"]) == "只有一个"


def test_real_skill_file_rules_extractable():
    from pathlib import Path
    from bjh.writer.prompts import SKILL_PATH_DEFAULT
    p = Path(SKILL_PATH_DEFAULT)
    if not p.exists():            # 从仓库根跑测试
        p = Path(__file__).resolve().parents[2] / SKILL_PATH_DEFAULT
    rules = extract_rules(p.read_text(encoding="utf-8"))
    for key in ["选题规则", "标题规则", "文案结构", "视频规范", "发布节奏"]:
        assert key in rules
