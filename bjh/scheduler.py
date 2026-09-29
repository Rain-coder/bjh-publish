# bjh/scheduler.py
import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from bjh.config import Config
from bjh.db import DB
from bjh.publisher.baijiahao import LoginExpiredError, PublishError, publish_video
from bjh.risk import contains_sensitive, next_publish_time

log = logging.getLogger("bjh.scheduler")


async def run_once(db: DB, cfg: Config, dry_run: bool = False, publish_fn=None) -> str:
    publish_fn = publish_fn or publish_video
    now = datetime.now()
    posts = db.due_posts(now.isoformat(timespec="seconds"))
    if not posts:
        return "idle"
    post = posts[0]
    account = db.get_account_by_id(post.account_id)
    rng = random.Random()

    if account is None:
        post.status, post.error = "failed", "账号不存在"
        db.update_post(post)
        return "failed"
    if account.status != "active":
        post.scheduled_at = next_publish_time(cfg, now, rng).isoformat(timespec="seconds")
        db.update_post(post)
        return "account-expired"
    if db.published_today(account.id) >= account.daily_limit:
        tomorrow650 = datetime.combine(now.date() + timedelta(days=1), time(6, 50))
        post.scheduled_at = next_publish_time(cfg, tomorrow650, rng).isoformat(timespec="seconds")
        db.update_post(post)
        return "daily-limit"

    video = db.get_video(post.video_id)
    draft = db.get_draft(video.draft_id)

    # 发布前最终风控：标题/文案敏感词检查（spec §5）
    hits = contains_sensitive(draft.title, cfg.sensitive_words) or \
        contains_sensitive(draft.script, cfg.sensitive_words)
    if hits:
        post.status, post.error = "failed", f"敏感词拦截: {','.join(hits)}"
        db.update_post(post)
        log.warning("敏感词拦截 post=%s words=%s", post.id, hits)
        return "sensitive"

    shot_dir = Path(cfg.data_dir) / "screenshots"
    if dry_run:
        await publish_fn(Path(video.path), draft.title, draft.tags,
                         Path(account.storage_state_path), shot_dir, dry_run=True)
        return "dry-run"

    post.status = "publishing"
    db.update_post(post)
    try:
        url = await publish_fn(Path(video.path), draft.title, draft.tags,
                               Path(account.storage_state_path), shot_dir)
        post.status, post.published_at, post.bjh_url = "published", \
            datetime.now().isoformat(timespec="seconds"), url
        db.update_post(post)
        return "published"
    except LoginExpiredError:
        db.set_account_status(account.id, "expired")
        post.status, post.error = "failed", "Cookie 失效，账号已标记 expired"
        db.update_post(post)
        log.error("Cookie 失效，账号 %s 标记 expired，post=%s", account.name, post.id)
        return "cookie-expired"
    except PublishError as e:
        post.status, post.error, post.screenshot_path = "failed", str(e), e.screenshot
        db.update_post(post)
        log.error("发布失败 post=%s: %s", post.id, e)
        return "failed"
    except Exception as e:                       # 非预期异常也落库，不阻塞队列
        post.status, post.error = "failed", f"未预期错误: {e}"
        db.update_post(post)
        return "failed"


async def run_daemon(db: DB, cfg: Config, dry_run: bool = False) -> None:
    log.info("调度器启动")
    stuck = db.fail_stuck_publishing()
    if stuck:
        log.warning("恢复 %d 条中断遗留的 publishing 记录", stuck)
    while True:
        try:
            db.fail_stuck_publishing()  # 兜底：上一轮 Ctrl-C/崩溃留下的 publishing
            result = await run_once(db, cfg, dry_run)
            if result != "idle":
                log.info("run_once -> %s", result)
        except Exception:
            log.exception("调度循环异常")
        await asyncio.sleep(60)
