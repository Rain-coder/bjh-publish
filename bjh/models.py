# bjh/models.py
from dataclasses import dataclass, field


@dataclass
class Account:
    name: str
    storage_state_path: str
    id: int | None = None
    daily_limit: int = 8
    last_publish_at: str | None = None
    status: str = "active"          # active | expired


@dataclass
class Topic:
    keyword: str
    title: str
    url: str
    source_author: str
    reads: int
    comments: int
    heat_score: float
    fetched_at: str                  # ISO
    id: int | None = None
    status: str = "new"              # new | selected | skipped


@dataclass
class Draft:
    topic_id: int
    title: str
    script: str
    tags: list[str]
    hook: str
    closing_question: str
    model: str
    cost: float
    status: str                      # ok | blocked | failed
    created_at: str
    id: int | None = None


@dataclass
class Video:
    draft_id: int
    path: str
    duration_sec: float
    size_bytes: int
    cost: float
    status: str                      # ok | failed
    error: str | None
    created_at: str
    id: int | None = None


@dataclass
class Post:
    video_id: int
    account_id: int
    scheduled_at: str
    id: int | None = None
    status: str = "queued"           # queued | publishing | published | failed
    published_at: str | None = None
    bjh_url: str | None = None
    error: str | None = None
    screenshot_path: str | None = None
