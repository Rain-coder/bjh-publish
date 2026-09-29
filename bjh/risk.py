# bjh/risk.py
import random
from datetime import datetime, time, timedelta

from bjh.config import Config


def contains_sensitive(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def random_interval_sec(cfg: Config, rng: random.Random) -> int:
    lo = cfg.interval_min_min * 60
    hi = max(cfg.interval_max_min * 60, lo)
    return rng.randint(lo, hi)


def _windows(cfg: Config, now: datetime) -> list[tuple[datetime, datetime]]:
    wins = []
    for day in (now.date(), now.date() + timedelta(days=1)):
        for h1, h2 in cfg.golden_hours:
            wins.append((datetime.combine(day, time(h1, 0)),
                         datetime.combine(day, time(h2, 0))))
    return wins


def next_publish_time(cfg: Config, now: datetime, rng: random.Random) -> datetime:
    cand = now + timedelta(seconds=random_interval_sec(cfg, rng))
    for s, e in _windows(cfg, now):
        if s <= cand < e:
            return cand
    for s, e in _windows(cfg, now):
        if cand < s <= cand + timedelta(hours=3):
            return s + timedelta(seconds=rng.randint(60, 600))
    return cand
