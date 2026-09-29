import random
from datetime import datetime
from bjh.config import Config
from bjh.risk import contains_sensitive, random_interval_sec, next_publish_time


def test_contains_sensitive():
    words = ["震惊", "必看"]
    assert contains_sensitive("震惊！月薪三万", words) == ["震惊"]
    assert contains_sensitive("普通标题", words) == []


def test_random_interval_bounds():
    cfg, rng = Config(), random.Random(42)
    for _ in range(20):
        v = random_interval_sec(cfg, rng)
        assert 25 * 60 <= v <= 50 * 60


def test_next_publish_time_in_golden_kept():
    cfg, rng = Config(), random.Random(42)
    now = datetime(2026, 9, 29, 8, 0)
    nxt = next_publish_time(cfg, now, rng)
    assert now < nxt <= now.replace(hour=8, minute=50)


def test_next_publish_time_shifts_to_golden():
    cfg, rng = Config(), random.Random(1)
    now = datetime(2026, 9, 29, 15, 0)          # 15:30 左右落点 → 18 点窗口 3h 内
    nxt = next_publish_time(cfg, now, rng)
    assert datetime(2026, 9, 29, 18, 0) <= nxt < datetime(2026, 9, 29, 18, 11)


def test_next_publish_time_far_from_golden_uses_raw():
    cfg, rng = Config(), random.Random(1)
    now = datetime(2026, 9, 29, 2, 0)           # 落点 ~2:30，下一窗口 7:00 相差 >3h
    nxt = next_publish_time(cfg, now, rng)
    assert now < nxt < now.replace(hour=3, minute=0)
