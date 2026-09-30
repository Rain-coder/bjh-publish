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


def test_delete_topic_cascades_blocked_drafts(tmp_path, monkeypatch):
    """删热榜条目：被拦截文案一并删，ok 文案及其话题保留。"""
    db, c = make(tmp_path, monkeypatch)
    t2 = Topic(keyword="养生", title="话题2", url="u2", source_author="a", reads=50,
               comments=5, heat_score=2.0, fetched_at="2026-09-30T10:00:00")
    db.insert_topic(t2)
    d2 = Draft(topic_id=2, title="被拦截", script="s", tags=[], hook="", closing_question="",
               model="m", cost=0.01, status="blocked", created_at="2026-09-30T10:00:00")
    db.insert_draft(d2)
    assert c.delete("/api/topics/2").json() == {"ok": True}
    assert db.get_topic(2) is None and db.get_draft(2) is None
    assert db.get_topic(1) is not None and db.get_draft(1) is not None
    assert c.delete("/api/topics/2").status_code == 404


def test_unblock_draft_rebuilds_video(tmp_path, monkeypatch):
    import time as _t
    import bjh.pipeline as pl
    from bjh.server import api as api_mod
    db, c = make(tmp_path, monkeypatch)
    assert c.post("/api/drafts/999/unblock").status_code == 404
    db.set_draft_status(1, "blocked")

    calls: list[int] = []

    class FakeVideo:
        id = 42

    async def fake_build(db_, cfg_, d_, t_):
        calls.append(d_.id)
        return FakeVideo()
    monkeypatch.setattr(pl, "build_video", fake_build)

    assert c.post("/api/drafts/1/unblock").status_code == 200
    for _ in range(150):                     # 等后台 job 线程完成
        if api_mod.JOB["running"] is None:
            break
        _t.sleep(0.02)
    assert calls == [1] and db.get_draft(1).status == "ok"
    assert api_mod.JOB["last"]["ok"] is True
    assert c.post("/api/drafts/1/unblock").status_code == 400   # 非 blocked 拒绝


def test_retry_failed_post(tmp_path, monkeypatch):
    import time as _t
    import bjh.scheduler as sched
    from bjh.server import api as api_mod
    db, c = make(tmp_path, monkeypatch)
    assert c.post("/api/posts/999/retry").status_code == 404
    pid = db.queue_post(1, 1, "2026-09-29T10:00:00")
    p = db.list_posts()[0]
    p.status, p.error = "failed", "发布超时"
    db.update_post(p)

    async def fake_run(db_, cfg_, dry_run=False, publish_fn=None):
        return "published"
    monkeypatch.setattr(sched, "run_once", fake_run)

    assert c.post(f"/api/posts/{pid}/retry").status_code == 200
    for _ in range(150):                     # 等后台 job 线程完成
        if api_mod.JOB["running"] is None:
            break
        _t.sleep(0.02)
    p2 = db.get_post(pid)
    assert p2.status == "queued" and p2.error is None
    assert p2.scheduled_at >= "2026-09-29T10:00:00"            # 已重置为点击时刻
    assert c.post(f"/api/posts/{pid}/retry").status_code == 400  # 非 failed 拒绝


def test_run_pipeline_publishes_one(tmp_path, monkeypatch):
    """一键全流程：生成入队后立即触发一次发布，不再需要手动「立即发布」。"""
    import time as _t
    import bjh.pipeline as pl
    import bjh.scheduler as sched
    from bjh.server import api as api_mod
    db, c = make(tmp_path, monkeypatch)
    fired: list[str] = []
    monkeypatch.setattr(pl, "run_pipeline",
                        lambda *a, **k: fired.append("make") or [7])

    async def fake_run(*a, **k):
        fired.append("publish")
        return "published"
    monkeypatch.setattr(sched, "run_once", fake_run)
    assert c.post("/api/run", json={"keyword": "养生"}).status_code == 200
    for _ in range(150):
        if api_mod.JOB["running"] is None:
            break
        _t.sleep(0.02)
    assert fired == ["make", "publish"]
    assert api_mod.JOB["last"]["ok"] and "已发布 1 条" in api_mod.JOB["last"]["result"]


def test_patch_post_reschedule(tmp_path, monkeypatch):
    import time as _t
    import bjh.scheduler as sched
    from bjh.server import api as api_mod
    db, c = make(tmp_path, monkeypatch)
    pid = db.queue_post(1, 1, "2026-09-30T10:00:00")
    # 宽松格式解析（未来时间 → 不触发发布）
    r = c.patch(f"/api/posts/{pid}", json={"scheduled_at": "2026-10-01 09:05"})
    assert r.json() == {"ok": True, "scheduled_at": "2026-10-01T09:05:00", "publishing": False}
    assert db.get_post(pid).scheduled_at == "2026-10-01T09:05:00"
    assert c.patch("/api/posts/999", json={"scheduled_at": "x"}).status_code == 404
    assert c.patch(f"/api/posts/{pid}", json={"scheduled_at": "乱写"}).status_code == 400

    # 改为当下/过去 → 立即触发一次发布
    fired: list[int] = []

    async def fake_run(*a, **k):
        fired.append(1)
        return "published"
    monkeypatch.setattr(sched, "run_once", fake_run)
    assert c.patch(f"/api/posts/{pid}",
                   json={"scheduled_at": "2026-09-29 00:00"}).json()["publishing"] is True
    for _ in range(150):
        if api_mod.JOB["running"] is None:
            break
        _t.sleep(0.02)
    assert fired == [1]

    # 非 queued 不可改
    p = db.get_post(pid)
    p.status = "published"
    db.update_post(p)
    assert c.patch(f"/api/posts/{pid}", json={"scheduled_at": "2026-10-01 09:05"}).status_code == 400
