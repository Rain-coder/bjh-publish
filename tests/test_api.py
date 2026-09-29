from datetime import datetime
from fastapi.testclient import TestClient
from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic, Video
from bjh.server.api import create_app


def make(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # save_config 写到 tmp 的 config.toml
    db = DB(tmp_path / "t.db")
    acc = db.upsert_account("default", daily_limit=8)
    t = Topic(keyword="职场", title="话题", url="u", source_author="a", reads=100,
              comments=10, heat_score=3.0, fetched_at="2026-09-29T10:00:00")
    db.insert_topic(t)
    d = Draft(topic_id=1, title="旧标题", script="s", tags=[], hook="", closing_question="",
              model="m", cost=0.02, status="ok", created_at="2026-09-29T10:00:00")
    db.insert_draft(d)
    v = Video(draft_id=1, path=str(tmp_path / "fake.mp4"), duration_sec=60.0,
              size_bytes=10, cost=0.02, status="ok", error=None,
              created_at="2026-09-29T10:00:00")
    db.insert_video(v)
    (tmp_path / "fake.mp4").write_bytes(b"0" * 64)
    return db, TestClient(create_app(db, Config()))


def test_topics_and_stats(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    assert c.get("/api/topics", params={"keyword": "职场"}).json()[0]["title"] == "话题"
    s = c.get("/api/stats").json()
    assert s["total_videos"] == 1 and abs(s["total_cost"] - 0.02) < 1e-9


def test_patch_draft_title(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    r = c.patch("/api/drafts/1", json={"title": "新标题"})
    assert r.json() == {"ok": True} and db.get_draft(1).title == "新标题"


def test_queue_post_and_video_file(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    pid = c.post("/api/posts", json={"video_id": 1}).json()["post_id"]
    assert db.list_posts()[0].id == pid
    assert c.get("/api/videos/1/file").status_code == 200


def test_patch_account_and_config(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    assert c.patch("/api/accounts/1", json={"daily_limit": 5}).json() == {"ok": True}
    assert db.get_account("default").daily_limit == 5
    r = c.patch("/api/config", json={"interval_min_min": 30, "interval_max_min": 60})
    assert r.json()["interval_min_min"] == 30
    toml = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "interval_min_min = 30" in toml
