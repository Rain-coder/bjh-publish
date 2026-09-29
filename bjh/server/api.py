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


class ConfigPatch(BaseModel):
    daily_limit: int | None = None
    interval_min_min: int | None = None
    interval_max_min: int | None = None


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


def create_app(db: DB, cfg: Config) -> FastAPI:
    app = FastAPI(title="bjh")

    @app.get("/api/topics")
    def topics(keyword: str | None = None, limit: int = 50):
        return [asdict(t) for t in db.list_topics(keyword, limit)]

    @app.get("/api/drafts")
    def drafts():
        return [asdict(d) for d in db.list_drafts(limit=200)]

    @app.patch("/api/drafts/{draft_id}")
    def patch_draft(draft_id: int, body: TitlePatch):
        if not db.get_draft(draft_id):
            raise HTTPException(404, "draft 不存在")
        db.update_draft_title(draft_id, body.title)
        return {"ok": True}

    @app.get("/api/videos")
    def videos():
        return db.videos_with_titles(limit=200)

    @app.get("/api/videos/{video_id}/file")
    def video_file(video_id: int):
        v = db.get_video(video_id)
        if not v or not v.path:
            raise HTTPException(404)
        return FileResponse(v.path, media_type="video/mp4")

    @app.get("/api/posts")
    def posts(status: str | None = None):
        return [asdict(p) for p in db.list_posts(status, limit=200)]

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
                "interval_max_min": cfg.interval_max_min}

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
            n = fetch_and_store(DB(Path(cfg.data_dir) / "bjh.db"), body.keyword, body.pages, cfg)
            return f"新增热文 {n} 条（{body.keyword}）"
        return _run_job(f"抓取:{body.keyword}", fn)

    @app.post("/api/create")
    def create(body: CreateReq):
        def fn():
            from bjh.pipeline import create_videos
            ids = create_videos(DB(Path(cfg.data_dir) / "bjh.db"), cfg, count=max(1, body.count))
            return f"生成视频 {len(ids)} 个: {ids or '（无可用选题/文案被拦截）'}"
        return _run_job("生成视频", fn)

    @app.post("/api/publish")
    def publish_now():
        def fn():
            from bjh.scheduler import run_once
            jdb = DB(Path(cfg.data_dir) / "bjh.db")
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
            ids = run_pipeline(DB(Path(cfg.data_dir) / "bjh.db"), cfg,
                               body.keyword, count=max(1, body.count), pages=body.pages)
            return f"流水线完成，视频 {ids} 已入队（用「立即发布」发出）"
        return _run_job(f"全流程:{body.keyword}", fn)

    @app.get("/api/job")
    def job():
        return {"running": JOB["running"], "started_at": JOB["started_at"], "last": JOB["last"]}

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app
