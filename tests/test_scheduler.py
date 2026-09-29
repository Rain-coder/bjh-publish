from datetime import datetime, timedelta
from pathlib import Path
import pytest
from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Video
from bjh.publisher.baijiahao import LoginExpiredError, PublishError
from bjh.scheduler import run_once


def seed(tmp_path, daily_limit=2):
    db = DB(tmp_path / "t.db")
    acc = db.upsert_account("default", daily_limit=daily_limit)
    d = Draft(topic_id=1, title="标题", script="s", tags=[], hook="", closing_question="",
              model="m", cost=0.01, status="ok", created_at="2026-09-29T10:00:00")
    db.insert_draft(d)
    v = Video(draft_id=d.id, path="data/videos/x.mp4", duration_sec=60.0, size_bytes=1,
              cost=0.01, status="ok", error=None, created_at="2026-09-29T10:00:00")
    db.insert_video(v)
    return db, acc, v


def past():
    return (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")


async def fake_ok(video_path, title, tags, state_path, shot_dir, dry_run=False):
    return "https://baijiahao.baidu.com/s?id=ok"


async def fake_fail(video_path, title, tags, state_path, shot_dir, dry_run=False):
    raise PublishError("boom", "shot.png")


async def test_run_once_publishes(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "published"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "published" and post.bjh_url.endswith("id=ok")
    assert db.published_today(acc.id) == 1


async def test_run_once_failure_recorded(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_fail)
    assert result == "failed"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "failed" and "boom" in post.error and post.screenshot_path


async def test_run_once_daily_limit_reschedules(tmp_path):
    db, acc, v = seed(tmp_path, daily_limit=1)
    db.queue_post(v.id, acc.id, past())
    await run_once(db, Config(), publish_fn=fake_ok)          # 今日 1/1
    v2 = Video(draft_id=1, path="p2", duration_sec=60.0, size_bytes=1, cost=0,
               status="ok", error=None, created_at="2026-09-29T11:00:00")
    db.insert_video(v2)
    pid = db.queue_post(v2.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "daily-limit"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "queued"
    assert post.scheduled_at > past()  # 顺延到未来（完整时间戳比较，防止静默 no-op）


async def test_run_once_expired_account(tmp_path):
    db, acc, v = seed(tmp_path)
    db.set_account_status(acc.id, "expired")
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "account-expired"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "queued"
    assert post.scheduled_at > past()  # 顺延到未来（完整时间戳比较，防止静默 no-op）


async def test_run_once_idle(tmp_path):
    db, acc, v = seed(tmp_path)
    assert await run_once(db, Config(), publish_fn=fake_ok) == "idle"


async def fake_login_expired(video_path, title, tags, state_path, shot_dir, dry_run=False):
    raise LoginExpiredError()


async def fake_bad_words(video_path, title, tags, state_path, shot_dir, dry_run=False):
    raise AssertionError("敏感词帖子不应调用 publish_fn")


def test_fail_stuck_publishing(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    post = db.due_posts(past())[0]
    post.status = "publishing"
    db.update_post(post)
    assert db.fail_stuck_publishing() == 1
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "failed" and post.error == "中断恢复"
    assert db.fail_stuck_publishing() == 0  # 幂等


async def test_run_once_cookie_expired_marks_account(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_login_expired)
    assert result == "cookie-expired"
    assert db.get_account_by_id(acc.id).status == "expired"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "failed" and "Cookie" in post.error


async def test_run_once_sensitive_guard_blocks_publish(tmp_path):
    db, acc, v = seed(tmp_path)
    d = db.get_draft(1)
    d.title = "震惊！必看干货"
    db.update_draft_title(d.id, d.title)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_bad_words)
    assert result == "sensitive"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "failed"
    assert "震惊" in post.error and "必看" in post.error
