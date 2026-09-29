from datetime import datetime
import pytest
from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic


def _seed(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_account("default")
    t = Topic(keyword="职场", title="一个话题标题", url="u", source_author="a",
              reads=1000, comments=50, heat_score=5.0,
              fetched_at="2026-09-29T10:00:00")
    db.insert_topic(t)
    return db, t


def test_create_videos_skips_blocked(tmp_path, monkeypatch):
    import bjh.pipeline as pl
    from bjh.models import Draft

    db, t = _seed(tmp_path)

    def fake_write(db, cfg, topic, client=None):
        d = Draft(topic_id=topic.id, title="x", script="x", tags=[], hook="",
                  closing_question="", model="m", cost=0.0, status="blocked",
                  created_at="2026-09-29T10:00:00")
        db.insert_draft(d)
        return d

    monkeypatch.setattr(pl, "write_draft", fake_write)
    ids = pl.create_videos(db, Config(), count=1)
    assert ids == []                                  # blocked → 不出片
    assert db.get_topic(t.id).status == "skipped"


def test_run_pipeline_queues_posts(tmp_path, monkeypatch):
    import bjh.pipeline as pl

    db, t = _seed(tmp_path)
    monkeypatch.setattr(pl, "fetch_and_store", lambda db, kw, pages, cfg: 3)
    monkeypatch.setattr(pl, "create_videos", lambda db, cfg, count=1, topic_id=None: [11])
    ids = pl.run_pipeline(db, Config(), keyword="职场", count=1)
    assert ids == [11]
    posts = db.list_posts()
    assert len(posts) == 1 and posts[0].status == "queued"
