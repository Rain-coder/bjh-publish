# bjh/server/api.py
import asyncio
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bjh.config import Config, save_config
from bjh.db import DB


class TitlePatch(BaseModel):
    title: str


class LimitPatch(BaseModel):
    daily_limit: int


class QueueReq(BaseModel):
    video_id: int


class PostPatch(BaseModel):
    scheduled_at: str


class ConfigPatch(BaseModel):
    daily_limit: int | None = None
    interval_min_min: int | None = None
    interval_max_min: int | None = None
    tts_voice: str | None = None


class FetchReq(BaseModel):
    keyword: str
    pages: int = 1


class CreateReq(BaseModel):
    count: int = 1


class RunReq(BaseModel):
    keyword: str
    count: int = 1
    pages: int = 1


# 后台任务状态（单并发；长任务开独立线程+独立 DB 连接，避免跨线程共享）
JOB = {"running": None, "started_at": None, "last": None}


def _job_db(cfg) -> "DB":
    """工作流端点共用的库句柄：data 目录可能不存在（换 cwd 启动/测试），先建。"""
    p = Path(cfg.data_dir) / "bjh.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return DB(p)


def _run_job(name: str, fn) -> dict:
    if JOB["running"]:
        raise HTTPException(409, f"任务进行中: {JOB['running']}，请等完成")
    JOB.update(running=name, started_at=datetime.now().isoformat(timespec="seconds"))

    def wrap():
        try:
            r = fn()
            JOB["last"] = {"name": name, "ok": True, "result": str(r),
                           "at": datetime.now().isoformat(timespec="seconds")}
        except Exception as e:
            JOB["last"] = {"name": name, "ok": False, "result": str(e)[:300],
                           "at": datetime.now().isoformat(timespec="seconds")}
        finally:
            JOB["running"] = None

    threading.Thread(target=wrap, daemon=True).start()
    return {"started": name}


def _scheduler_loop(cfg: Config) -> None:
    """面板内常驻调度：每 60s 查到期帖子，空闲则发布（不用再单开 bjh publish --daemon）。"""
    import time as _t

    def loop():
        while True:
            _t.sleep(60)
            try:
                if JOB["running"]:
                    continue
                if _job_db(cfg).due_posts(datetime.now().isoformat(timespec="seconds")):
                    def fn():
                        from bjh.scheduler import run_once
                        return asyncio.run(run_once(_job_db(cfg), cfg))
                    _run_job("定时发布", fn)          # 撞锁会 409，下轮再试
            except HTTPException:
                pass
            except Exception:
                pass                                  # 调度循环永不退出

    threading.Thread(target=loop, daemon=True).start()


def create_app(db: DB, cfg: Config) -> FastAPI:
    app = FastAPI(title="bjh")
    _scheduler_loop(cfg)

    @app.get("/api/topics")
    def topics(keyword: str | None = None, limit: int = 50):
        out = []
        for t in db.list_topics(keyword, limit):
            d = asdict(t)
            d["blocked_draft_id"] = db.blocked_draft_id(t.id)
            out.append(d)
        return out

    @app.delete("/api/topics/{topic_id}")
    def del_topic(topic_id: int):
        if not db.get_topic(topic_id):
            raise HTTPException(404, "话题不存在")
        db.delete_topic(topic_id)
        return {"ok": True}

    @app.get("/api/drafts")
    def drafts():
        return [asdict(d) for d in db.list_drafts(limit=200)]

    @app.patch("/api/drafts/{draft_id}")
    def patch_draft(draft_id: int, body: TitlePatch):
        if not db.get_draft(draft_id):
            raise HTTPException(404, "draft 不存在")
        db.update_draft_title(draft_id, body.title)
        return {"ok": True}

    @app.post("/api/drafts/{draft_id}/unblock")
    def unblock_draft(draft_id: int):
        """解除敏感词拦截：文案置 ok 并后台重建视频。"""
        d = db.get_draft(draft_id)
        if not d:
            raise HTTPException(404, "文案不存在")
        if d.status != "blocked":
            raise HTTPException(400, f"文案状态是 {d.status}，无需解除")

        def fn():
            import asyncio
            from bjh.pipeline import build_video
            t = db.get_topic(d.topic_id)
            if not t:
                raise RuntimeError("话题已被删除，无法重建视频")
            db.set_draft_status(draft_id, "ok")
            db.set_topic_status(t.id, "selected")
            video = asyncio.run(build_video(db, cfg, d, t))
            if not video:
                raise RuntimeError("视频重建失败，看日志")
            return f"已解除拦截并重建视频 #{video.id}，可入队发布"
        return _run_job(f"解除拦截:{draft_id}", fn)

    @app.get("/api/videos")
    def videos():
        return db.videos_with_titles(limit=200)

    @app.get("/api/videos/{video_id}/file")
    def video_file(video_id: int):
        v = db.get_video(video_id)
        if not v or not v.path:
            raise HTTPException(404)
        return FileResponse(v.path, media_type="video/mp4")

    @app.delete("/api/videos/{video_id}")
    def delete_video(video_id: int):
        import shutil
        v = db.get_video(video_id)
        if not v:
            raise HTTPException(404, "视频不存在")
        # 已发布视频也允许删（仅清本地记录和文件，百家号平台上的帖子不受影响）
        db.delete_posts_for_video(video_id)
        db.delete_video(video_id)
        # 删除磁盘产物：视频文件所在工作目录（音频/图片/字幕），约束在 data/videos 内防误删
        if v.path:
            f = Path(v.path)
            base = (Path(cfg.data_dir) / "videos").resolve()
            if base in f.resolve().parents and f.exists():
                shutil.rmtree(f.parent)
        return {"ok": True}

    @app.get("/api/posts")
    def posts(status: str | None = None):
        return [asdict(p) for p in db.list_posts(status, limit=200)]

    @app.patch("/api/posts/{post_id}")
    def patch_post(post_id: int, body: PostPatch):
        """修订排队帖子的发布时间；设为当下或过去即可被下个调度周期立即发出。"""
        p = db.get_post(post_id)
        if not p:
            raise HTTPException(404, "帖子不存在")
        if p.status != "queued":
            raise HTTPException(400, f"帖子状态是 {p.status}，只能改排队中的时间")
        # 宽松解析 "2026-09-30 15:00" / isoformat
        s = body.scheduled_at.strip().replace(" ", "T")
        try:
            ts = datetime.fromisoformat(s if "T" in s else f"{s}T00:00:00")
        except ValueError:
            raise HTTPException(400, "时间格式不对，示例 2026-09-30 15:00")
        p.scheduled_at = ts.isoformat(timespec="seconds")
        db.update_post(p)
        now_publish = ts <= datetime.now()
        if now_publish:      # 时间设为当下/过去 = 想现在发，直接触发（有任务在跑则等调度线程）
            def fn():
                from bjh.scheduler import run_once
                return asyncio.run(run_once(db, cfg))
            try:
                _run_job(f"立即发布:{post_id}", fn)
            except HTTPException:
                pass
        return {"ok": True, "scheduled_at": p.scheduled_at, "publishing": now_publish}

    @app.post("/api/posts/{post_id}/retry")
    def retry_post(post_id: int):
        """失败帖子重发：重置为 queued（scheduled_at=now）并立刻走一次发布流程。"""
        p = db.get_post(post_id)
        if not p:
            raise HTTPException(404, "帖子不存在")
        if p.status != "failed":
            raise HTTPException(400, f"帖子状态是 {p.status}，只有失败的帖子能重发")
        p.status, p.error = "queued", None
        p.scheduled_at = datetime.now().isoformat(timespec="seconds")
        db.update_post(p)

        def fn():
            from bjh.scheduler import run_once
            return asyncio.run(run_once(db, cfg))
        return _run_job(f"重发:{post_id}", fn)

    @app.post("/api/posts")
    def queue(body: QueueReq):
        v = db.get_video(body.video_id)
        if not v:
            raise HTTPException(404, "视频不存在")
        if v.status != "ok":
            raise HTTPException(400, f"视频状态异常: {v.status}")
        pid = db.queue_post(body.video_id, 1, datetime.now().isoformat(timespec="seconds"))
        return {"post_id": pid}

    @app.get("/api/screenshots/{name}")
    def screenshot(name: str):
        base = (Path(cfg.data_dir) / "screenshots").resolve()
        target = (base / name).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(404, "截图不存在")
        return FileResponse(target, media_type="image/png")

    @app.get("/api/accounts")
    def accounts():
        return [asdict(a) for a in db.list_accounts()]

    @app.patch("/api/accounts/{account_id}")
    def patch_account(account_id: int, body: LimitPatch):
        db.set_account_daily_limit(account_id, body.daily_limit)
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        return {"daily_limit": cfg.daily_limit, "interval_min_min": cfg.interval_min_min,
                "interval_max_min": cfg.interval_max_min, "tts_voice": cfg.tts_voice}

    @app.patch("/api/config")
    def patch_config(body: ConfigPatch):
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(cfg, k, v)
        save_config(cfg)
        return get_config()

    @app.get("/api/stats")
    def stats():
        return db.stats()

    # ==== 工作流操作（后台线程执行，GET /api/job 查进度）====
    @app.post("/api/fetch")
    def fetch(body: FetchReq):
        def fn():
            from bjh.pipeline import fetch_and_store
            n = fetch_and_store(_job_db(cfg), body.keyword, body.pages, cfg)
            return f"新增热文 {n} 条（{body.keyword}）"
        return _run_job(f"抓取:{body.keyword}", fn)

    @app.post("/api/create")
    def create(body: CreateReq):
        def fn():
            from bjh.pipeline import create_videos
            ids = create_videos(_job_db(cfg), cfg, count=max(1, body.count))
            return f"生成视频 {len(ids)} 个: {ids or '（无可用选题/文案被拦截）'}"
        return _run_job("生成视频", fn)

    @app.post("/api/publish")
    def publish_now():
        def fn():
            from bjh.scheduler import run_once
            jdb = _job_db(cfg)
            acc = jdb.get_account("default") or (jdb.list_accounts() or [None])[0]
            vids = [v["id"] for v in jdb.videos_with_titles(limit=500)
                    if v["status"] == "ok" and v["id"] not in
                    {p.video_id for p in jdb.list_posts(limit=500)}]
            if not vids:
                return "没有待发视频，先「生成视频」"
            jdb.queue_post(vids[-1], acc.id, datetime.now().isoformat(timespec="seconds"))  # 最旧优先
            return asyncio.run(run_once(jdb, cfg))
        return _run_job("立即发布", fn)

    @app.post("/api/run")
    def run_all(body: RunReq):
        def fn():
            from bjh.pipeline import run_pipeline
            from bjh.scheduler import run_once
            jdb = _job_db(cfg)
            ids = run_pipeline(jdb, cfg, body.keyword,
                               count=max(1, body.count), pages=body.pages)
            if not ids:
                return "没有生成视频（无可用选题/文案被拦截）"
            result = asyncio.run(run_once(jdb, cfg))       # 立即发最旧一条
            rest = len(ids) - 1
            return (f"流水线完成，视频 {ids}：已发布 1 条（{result}），"
                    + (f"其余 {rest} 条排队等调度" if rest > 0 else "全部发完"))
        return _run_job(f"全流程:{body.keyword}", fn)

    @app.get("/api/job")
    def job():
        return {"running": JOB["running"], "started_at": JOB["started_at"], "last": JOB["last"]}

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app
