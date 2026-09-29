# bjh/writer/service.py
from datetime import datetime

from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic
from bjh.risk import contains_sensitive
from bjh.writer.deepseek import DeepSeekClient, estimate_cost
from bjh.writer.prompts import build_system_prompt, build_user_prompt, load_rules, parse_llm_json, pick_title


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
