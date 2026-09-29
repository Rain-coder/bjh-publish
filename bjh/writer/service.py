# bjh/writer/service.py
from datetime import datetime

from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic
from bjh.risk import contains_sensitive
from bjh.writer.deepseek import DeepSeekClient, estimate_cost
from bjh.writer.prompts import build_system_prompt, build_user_prompt, load_rules, parse_llm_json, pick_title


IMAGE_QUERY_PROMPT = (
    "你是短视频配图检索词生成器。把口播文案按叙述顺序均分成 {n} 段，"
    "为每段生成 1 个英文图片搜索词（2-4 个单词，具体到人物/物体/场景，适合图库检索，"
    "避免抽象词；只准使用英文字母和空格，禁止中文/拼音）。"
    "返回 JSON: {{\"queries\": [\"...\"]}}，恰好 {n} 个，不要解释。")

TRANSLATE_NOTE = ("\n上一版输出含非英文搜索词，请把每个搜索词翻译成地道英文后重新输出，"
                  "仍是 JSON 且恰好 {n} 个。")


def _has_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in s)


def image_queries(client: DeepSeekClient, script: str, n: int) -> list[str] | None:
    """按文案分段生成英文配图搜索词，使第 i 张图对应第 i 段旁白；失败返回 None（退回话题关键词）。"""
    import json
    import re
    base = IMAGE_QUERY_PROMPT.format(n=n) + "\n文案：\n" + script
    try:
        for attempt in (1, 2):
            raw, _ = client.chat([{"role": "user", "content": base}], json_mode=True)
            text = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
            qs = [str(q).strip() for q in json.loads(text).get("queries", []) if str(q).strip()]
            qs = [q for q in qs if not _has_cjk(q)]     # Pexels 只认英文，中文词等同无效
            if qs:
                return [qs[i % len(qs)] for i in range(n)]   # 不足 n 个则循环复用，保证段序对应
            base = base + TRANSLATE_NOTE.format(n=n)    # 全军覆没 → 明确要求翻译重试一次
    except Exception:
        return None
    return None


def write_draft(db: DB, cfg: Config, topic: Topic,
                client: DeepSeekClient | None = None) -> Draft:
    if client is None:
        if not cfg.deepseek_api_key:
            raise ValueError("缺少 deepseek_api_key（config.toml 或 BJH_DEEPSEEK_API_KEY）")
        client = DeepSeekClient(cfg.deepseek_api_key, cfg.deepseek_base_url, cfg.deepseek_model)
    if topic.id is None:                    # 测试/调用方可能传入未入库话题
        db.insert_topic(topic)
    system = build_system_prompt(load_rules())
    user = build_user_prompt(topic)
    cost = 0.0
    for attempt in (1, 2):
        raw, usage = client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True)
        cost += estimate_cost(usage, cfg)
        try:
            data = parse_llm_json(raw)
        except ValueError:
            if attempt == 2:
                return _insert(db, topic, title="", script="", tags=[], hook="",
                               closing_question="", status="failed", cost=cost)
            continue
        title = data["title"].strip() or pick_title(data["titles"])
        hits = contains_sensitive(title + data["script"], cfg.sensitive_words)
        if not hits:
            return _insert(db, topic, title=title, script=data["script"], tags=data["tags"],
                           hook=data["hook"], closing_question=data["closing_question"],
                           status="ok", cost=cost)
        user = user + f"\n上一版命中平台违禁词{hits}，请彻底换角度重写。"
    return _insert(db, topic, title="", script="", tags=[], hook="",
                   closing_question="", status="blocked", cost=cost)


def _insert(db, topic, *, title, script, tags, hook, closing_question, status, cost) -> Draft:
    d = Draft(topic_id=topic.id, title=title, script=script, tags=tags, hook=hook,
              closing_question=closing_question, model="deepseek-chat",
              cost=round(cost, 6), status=status,
              created_at=datetime.now().isoformat(timespec="seconds"))
    db.insert_draft(d)
    return d
