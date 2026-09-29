# bjh/writer/prompts.py
import json
import re
from pathlib import Path

from bjh.models import Topic

SKILL_PATH_DEFAULT = "skills/bjh-video/SKILL.md"
REQUIRED_KEYS = ("titles", "title", "hook", "script", "closing_question", "tags")
SCRIPT_MIN, SCRIPT_MAX = 150, 320   # 字数契约（60~90s 口播）
TITLE_MAX = 28


def extract_rules(skill_md: str) -> str:
    m = re.search(r"<rules>(.*?)</rules>", skill_md, re.S)
    if not m:
        raise ValueError("SKILL.md 缺少 <rules> 段")
    return m.group(1).strip()


def build_system_prompt(rules: str) -> str:
    return (
        "你是百家号爆款口播视频策划。严格遵循以下平台算法规则创作：\n\n"
        f"{rules}\n\n"
        "输出必须是合法 JSON（不要输出其他内容），字段：titles(3个候选), title(最终), "
        "hook, script(180~260字口播稿), closing_question, tags(2~4个，不含#)。"
        f"约束：title ≤ {TITLE_MAX} 字；script 每句 ≤20 字且口语化；"
        "结尾提问必须是 A/B 站队式开放式问题。"
    )


def build_user_prompt(topic: Topic) -> str:
    ratio = (topic.comments / topic.reads * 100) if topic.reads else 0.0
    return (
        f"参考热门文章（禁止搬运原句，必须原创重写）：\n"
        f"标题：{topic.title}\n阅读 {topic.reads}，评论 {topic.comments}（评论比 {ratio:.2f}%）\n"
        f"关键词：{topic.keyword}\n"
        "请按规则围绕该话题写一条全新角度的口播视频文案。"
    )


def parse_llm_json(raw: str) -> dict:
    text = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出非合法 JSON: {e}") from e
    for k in REQUIRED_KEYS:
        if k not in d:
            raise ValueError(f"LLM 输出缺少字段 {k}")
    if not isinstance(d["titles"], list) or len(d["titles"]) < 3:
        raise ValueError("titles 必须是 3 个候选")
    if not (SCRIPT_MIN <= len(d["script"]) <= SCRIPT_MAX):
        raise ValueError(f"script 字数 {len(d['script'])} 不在 {SCRIPT_MIN}~{SCRIPT_MAX}")
    if len(d["title"]) > TITLE_MAX:
        raise ValueError(f"title 超过 {TITLE_MAX} 字")
    return d


def load_rules(path: str | None = None) -> str:
    p = Path(path or SKILL_PATH_DEFAULT)
    if not p.exists():
        p = Path(__file__).resolve().parents[2] / SKILL_PATH_DEFAULT
    return extract_rules(p.read_text(encoding="utf-8"))


def pick_title(titles: list[str]) -> str:
    for t in titles:                       # 悬念式优先（含问号）
        if "？" in t or "?" in t:
            return t
    return titles[0]
