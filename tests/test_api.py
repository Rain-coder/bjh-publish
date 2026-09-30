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
    r = c.patch("/api/config", json={"interval_min_min": 30, "interval_max_min": 60,
                                     "tts_voice": "zh-CN-YunxiNeural"})
    assert r.json()["interval_min_min"] == 30
    assert r.json()["tts_voice"] == "zh-CN-YunxiNeural"
    toml = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "interval_min_min = 30" in toml
    assert 'tts_voice = "zh-CN-YunxiNeural"' in toml


def test_screenshot_route_serves_and_blocks_traversal(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    shots = tmp_path / "data" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "fail_x.png").write_bytes(b"png")
    r = c.get("/api/screenshots/fail_x.png")
    assert r.status_code == 200 and r.content == b"png"
    assert c.get("/api/screenshots/nope.png").status_code == 404
    # 路径穿越与目录本身均被拒
    assert c.get("/api/screenshots/..%2F..%2Ft.db").status_code == 404
    assert c.get("/api/screenshots/..%2Faccounts").status_code == 404


def test_queue_post_validates_video(tmp_path, monkeypatch):
    db, c = make(tmp_path, monkeypatch)
    assert c.post("/api/posts", json={"video_id": 999}).status_code == 404
    v = db.get_video(1)
    db.conn.execute("UPDATE videos SET status='failed' WHERE id=1")
    db.conn.commit()
    assert c.post("/api/posts", json={"video_id": 1}).status_code == 400
    assert db.list_posts() == []


def test_delete_video_cascades_queue_and_files(tmp_path, monkeypatch):
    from datetime import datetime
    db, c = make(tmp_path, monkeypatch)
    # 把视频文件放进 data/videos/draft_1/ 工作目录内（删除应连目录一起清）
    work = tmp_path / "data" / "videos" / "draft_1"
    work.mkdir(parents=True)
    f = work / "1.mp4"
    f.write_bytes(b"0" * 32)
    db.conn.execute("UPDATE videos SET path=? WHERE id=1", (str(f),))
    db.conn.commit()
    db.queue_post(1, 1, datetime.now().isoformat(timespec="seconds"))
    assert c.delete("/api/videos/1").json() == {"ok": True}
    assert db.get_video(1) is None and db.list_posts() == []
    assert not work.exists() and not f.exists()
    assert c.delete("/api/videos/1").status_code == 404


def test_delete_video_allows_published(tmp_path, monkeypatch):
    """已发布视频也可删：本地记录+帖子级联清理，不影响平台。"""
    db, c = make(tmp_path, monkeypatch)
    pid = db.queue_post(1, 1, "2026-09-29T10:00:00")
    p = db.list_posts()[0]
    p.status, p.bjh_url = "published", "https://bjh.baidu.com/s?id=1"
    db.update_post(p)
    assert c.delete("/api/videos/1").json() == {"ok": True}
    assert db.get_video(1) is None and db.list_posts() == []


def test_delete_video_ignores_files_outside_videos_dir(tmp_path, monkeypatch):
    """视频路径在 data/videos 之外（如测试残留）时只删记录，不动文件。"""
    db, c = make(tmp_path, monkeypatch)
    outside = tmp_path / "outside.mp4"
    assert c.delete("/api/videos/1").json() == {"ok": True}
    assert db.get_video(1) is None and outside.parent.exists()
