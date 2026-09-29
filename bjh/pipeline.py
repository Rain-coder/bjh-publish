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
from bjh.writer.service import write_draft
from bjh.writer.topic import dedupe_topics, select_topics


async def build_video(db: DB, cfg: Config, draft: Draft, topic: Topic) -> Video | None:
    work = Path(cfg.data_dir) / "videos" / f"draft_{draft.id}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        mp3 = work / "voice.mp3"
        words = await synthesize_with_retry(draft.script, cfg.tts_voice, mp3)
        sents = sentences_with_timing(words, split_sentences(draft.script))
        ass = work / "subs.ass"
        ass.write_text(build_ass(sents), encoding="utf-8")
        safe_ass = work / "subs.ass"        # 简单文件名，避免 subtitles 滤镜路径转义问题
        images = await collect_images(topic.keyword, 6, work / "img",
                                      cfg.pexels_api_key or None,
                                      [draft.title, draft.hook or draft.title])
        out = work / f"{draft.id}.mp4"
        compose_video(mp3, images, safe_ass, out)
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
