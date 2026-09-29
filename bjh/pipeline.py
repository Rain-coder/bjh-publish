# bjh/pipeline.py
import random
from datetime import datetime
from pathlib import Path

from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic, Video
from bjh.risk import next_publish_time
from bjh.studio.compose import audio_duration, compose_video
from bjh.studio.images import collect_images
from bjh.studio.subtitle import build_ass
from bjh.studio.tts import split_sentences, sentences_with_timing, synthesize_with_retry
from bjh.writer.deepseek import DeepSeekClient
from bjh.writer.service import image_queries, write_draft
from bjh.writer.topic import dedupe_topics, select_topics

N_IMAGES = 6


def segment_texts(script: str, n: int, limit: int = 12) -> list[str]:
    """文案按句均分成 n 段取开头字样（兜底卡片/配图分段用，与画面时段一一对应）。"""
    sents = split_sentences(script) or [script]
    per = max(1, round(len(sents) / n))
    groups = ["".join(sents[i * per:(i + 1) * per]) for i in range(n)]
    texts = [g[:limit].strip() or script[:limit] for g in groups if g.strip()]
    while len(texts) < n:
        texts.append(script[:limit])
    return texts


async def build_video(db: DB, cfg: Config, draft: Draft, topic: Topic) -> Video | None:
    work = Path(cfg.data_dir) / "videos" / f"draft_{draft.id}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        mp3 = work / "voice.mp3"
        words = await synthesize_with_retry(draft.script, cfg.tts_voice, mp3, rate=cfg.tts_rate)
        sents = sentences_with_timing(words, split_sentences(draft.script))
        ass = work / "subs.ass"        # 简单文件名，避免 subtitles 滤镜路径转义问题
        ass.write_text(build_ass(sents), encoding="utf-8")
        # 配图与文案分段对应：DeepSeek 按旁白顺序生成 N 个英文搜索词（失败退回话题关键词）
        queries = None
        if cfg.pexels_api_key and cfg.deepseek_api_key:
            client = DeepSeekClient(cfg.deepseek_api_key, cfg.deepseek_base_url, cfg.deepseek_model)
            queries = image_queries(client, draft.script, N_IMAGES)
        # 文生图兜底：搜不到（或 always 模式）时按对应段落文字生成
        generate = None
        if cfg.imagegen_api_key and cfg.imagegen_mode != "off":
            from bjh.studio.imagegen import generate_image

            async def generate(prompt: str, out_path: Path):
                return await generate_image(
                    prompt + "。竖版短视频配图，真实感摄影风格，画面干净无文字", out_path, cfg)
        images = await collect_images(topic.keyword, N_IMAGES, work / "img",
                                      cfg.pexels_api_key or None,
                                      segment_texts(draft.script, N_IMAGES, limit=40),
                                      queries=queries, generate=generate,
                                      gen_first=cfg.imagegen_mode == "always")
        out = work / f"{draft.id}.mp4"
        compose_video(mp3, images, ass, out)
        v = Video(draft_id=draft.id, path=str(out), duration_sec=audio_duration(out),
                  size_bytes=out.stat().st_size, cost=draft.cost, status="ok",
                  error=None, created_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:
        v = Video(draft_id=draft.id, path="", duration_sec=0.0, size_bytes=0,
                  cost=0.0, status="failed", error=str(e),
                  created_at=datetime.now().isoformat(timespec="seconds"))
    db.insert_video(v)
    return v if v.status == "ok" else None


def create_videos(db: DB, cfg: Config, count: int = 1,
                  topic_id: int | None = None) -> list[int]:
    import asyncio
    if topic_id is not None:
        topics = [db.get_topic(topic_id)]
    else:
        used = [d.title for d in db.list_drafts(limit=500)]
        topics = select_topics(db.unused_topics(n=count * 5), n=count, used_titles=used)
    video_ids: list[int] = []
    for t in topics:
        if not t:
            continue
        db.set_topic_status(t.id, "selected")
        draft = write_draft(db, cfg, t)
        if draft.status != "ok":
            db.set_topic_status(t.id, "skipped")
            continue
        video = asyncio.run(build_video(db, cfg, draft, t))
        if video:
            video_ids.append(video.id)
        else:
            db.set_topic_status(t.id, "skipped")
    return video_ids


def fetch_and_store(db: DB, keyword: str, pages: int, cfg: Config) -> int:
    import asyncio
    from bjh.fetcher.baijiahao import fetch_topics
    from bjh.publisher.baijiahao import LoginExpiredError
    acc = db.get_account("default") or (db.list_accounts() or [None])[0]
    if not acc:
        raise SystemExit("无账号，请先 bjh login")
    try:
        raw = asyncio.run(fetch_topics(keyword, pages, acc))
    except LoginExpiredError:
        db.set_account_status(acc.id, "expired")
        raise SystemExit(f"账号 [{acc.name}] Cookie 已失效，已标记 expired，请重新 bjh login")
    seen = {t.title for t in db.list_topics(keyword, limit=500)}
    fresh = [t for t in dedupe_topics(raw) if t.title not in seen]
    for t in fresh:
        db.insert_topic(t)
    return len(fresh)


def run_pipeline(db: DB, cfg: Config, keyword: str, count: int = 1, pages: int = 1) -> list[int]:
    acc = db.get_account("default") or (db.list_accounts() or [None])[0]
    if not acc:
        raise SystemExit("无账号，请先 bjh login")
    fetch_and_store(db, keyword, pages, cfg)
    video_ids = create_videos(db, cfg, count=count)
    rng = random.Random()
    t = next_publish_time(cfg, datetime.now(), rng)
    for vid in video_ids:
        db.queue_post(vid, acc.id, t.isoformat(timespec="seconds"))
        t = next_publish_time(cfg, t, rng)
    return video_ids
