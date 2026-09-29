from pathlib import Path
from bjh.db import DB
from bjh.models import Account, Topic, Draft, Video, Post


def make_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "test.db")


def test_account_roundtrip(tmp_path):
    db = make_db(tmp_path)
    acc = db.upsert_account("main", daily_limit=5)
    assert acc.id is not None and acc.daily_limit == 5
    db.set_account_status(acc.id, "expired")
    db.set_account_daily_limit(acc.id, 9)
    got = db.get_account("main")
    assert got.status == "expired" and got.daily_limit == 9
    assert db.get_account_by_id(got.id).name == "main"


def test_topic_select_and_draft_video_post_flow(tmp_path):
    db = make_db(tmp_path)
    db.upsert_account("main")
    for i, (r, c) in enumerate([(100000, 20), (5000, 300), (80000, 900)]):
        t = Topic(id=None, keyword="kw", title=f"标题{i}", url=f"http://x/{i}",
                  source_author="a", reads=r, comments=c,
                  heat_score=0.0, fetched_at="2026-09-29T10:00:00", status="new")
        t.heat_score = 0.0
        db.insert_topic(t)
    best = db.unused_topics(n=2)
    assert best[0].comments == 900            # 热度排序：评论权重高者优先
    db.set_topic_status(best[0].id, "selected")
    assert all(t.status == "new" for t in db.unused_topics())

    d = Draft(id=None, topic_id=best[0].id, title="爆款标题", script="口播稿" * 40,
              tags=["情感"], hook="你敢信？", closing_question="你怎么看？",
              model="deepseek-chat", cost=0.02, status="ok", created_at="2026-09-29T10:05:00")
    did = db.insert_draft(d)
    db.update_draft_title(did, "新标题")
    assert db.get_draft(did).title == "新标题"

    v = Video(id=None, draft_id=did, path="data/videos/x.mp4", duration_sec=75.0,
              size_bytes=100, cost=0.02, status="ok", error=None,
              created_at="2026-09-29T10:10:00")
    vid = db.insert_video(v)
    assert db.videos_with_titles()[0]["draft_title"] == "新标题"

    pid = db.queue_post(vid, 1, "2026-09-29T11:00:00")
    posts = db.due_posts("2026-09-29T12:00:00")
    assert len(posts) == 1 and posts[0].id == pid and posts[0].status == "queued"
    posts[0].status = "published"
    posts[0].published_at = "2026-09-29T12:01:00"
    posts[0].bjh_url = "https://baijiahao.baidu.com/s?id=1"
    db.update_post(posts[0])
    assert db.published_today(1) == 1
    s = db.stats()
    # stats 只汇总 videos.cost（build_video 会把 draft.cost 记入 video.cost）
    assert s["total_videos"] == 1 and abs(s["total_cost"] - 0.02) < 1e-9
