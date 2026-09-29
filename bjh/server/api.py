# bjh/server/api.py
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
        pid = db.queue_post(body.video_id, 1, datetime.now().isoformat(timespec="seconds"))
        return {"post_id": pid}

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

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app
