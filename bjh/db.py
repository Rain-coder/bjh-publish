# bjh/db.py
import json
import sqlite3
from pathlib import Path

from bjh.models import Account, Topic, Draft, Video, Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  storage_state_path TEXT NOT NULL,
  daily_limit INTEGER DEFAULT 8,
  last_publish_at TEXT,
  status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS topics(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
  source_author TEXT DEFAULT '', reads INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
  heat_score REAL DEFAULT 0, fetched_at TEXT NOT NULL,
  status TEXT DEFAULT 'new');
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL, title TEXT NOT NULL, script TEXT NOT NULL,
  tags TEXT DEFAULT '[]', hook TEXT DEFAULT '', closing_question TEXT DEFAULT '',
  model TEXT DEFAULT '', cost REAL DEFAULT 0,
  status TEXT DEFAULT 'ok', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS videos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL, path TEXT NOT NULL, duration_sec REAL DEFAULT 0,
  size_bytes INTEGER DEFAULT 0, cost REAL DEFAULT 0,
  status TEXT DEFAULT 'ok', error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
  status TEXT DEFAULT 'queued', scheduled_at TEXT NOT NULL,
  published_at TEXT, bjh_url TEXT, error TEXT, screenshot_path TEXT);
"""

_COLS = {  # 表 -> (dataclass, 列顺序)
    "accounts": (Account, "id,name,storage_state_path,daily_limit,last_publish_at,status"),
    "topics": (Topic, "id,keyword,title,url,source_author,reads,comments,heat_score,fetched_at,status"),
    "drafts": (Draft, "id,topic_id,title,script,tags,hook,closing_question,model,cost,status,created_at"),
    "videos": (Video, "id,draft_id,path,duration_sec,size_bytes,cost,status,error,created_at"),
    "posts": (Post, "id,video_id,account_id,status,scheduled_at,published_at,bjh_url,error,screenshot_path"),
}


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- 通用行转 dataclass ----
    def _row(self, table: str, row) -> object:
        cls, _ = _COLS[table]
        d = dict(row)
        if "tags" in d:
            d["tags"] = json.loads(d["tags"] or "[]")
        return cls(**d)

    def _insert(self, table: str, obj) -> int:
        _, cols = _COLS[table]
        names = [c for c in cols.split(",") if c != "id"]
        vals = []
        for n in names:
            v = getattr(obj, n)
            if isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        cur = self.conn.execute(
            f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' * len(names))})", vals)
        obj.id = cur.lastrowid
        self.conn.commit()
        return obj.id

    def _get(self, table: str, where: str, arg) -> object | None:
        _, cols = _COLS[table]
        row = self.conn.execute(f"SELECT {cols} FROM {table} WHERE {where}", (arg,)).fetchone()
        return self._row(table, row) if row else None

    # ---- accounts ----
    def upsert_account(self, name: str, daily_limit: int = 8) -> Account:
        acc = self.get_account(name)
        if acc:
            return acc
        acc = Account(name=name, storage_state_path=f"data/accounts/{name}.json",
                      daily_limit=daily_limit)
        self._insert("accounts", acc)
        return acc

    def get_account(self, name: str) -> Account | None:
        return self._get("accounts", "name=?", name)

    def get_account_by_id(self, account_id: int) -> Account | None:
        return self._get("accounts", "id=?", account_id)

    def list_accounts(self) -> list[Account]:
        _, cols = _COLS["accounts"]
        return [self._row("accounts", r) for r in
                self.conn.execute(f"SELECT {cols} FROM accounts ORDER BY id")]

    def set_account_status(self, account_id: int, status: str) -> None:
        self.conn.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))
        self.conn.commit()

    def set_account_daily_limit(self, account_id: int, limit: int) -> None:
        self.conn.execute("UPDATE accounts SET daily_limit=? WHERE id=?", (limit, account_id))
        self.conn.commit()

    # ---- topics ----
    def insert_topic(self, t: Topic) -> int:
        return self._insert("topics", t)

    def list_topics(self, keyword: str | None = None, limit: int = 100) -> list[Topic]:
        _, cols = _COLS["topics"]
        if keyword:
            rows = self.conn.execute(
                f"SELECT {cols} FROM topics WHERE keyword=? "
                "ORDER BY heat_score DESC, comments DESC LIMIT ?",
                (keyword, limit))
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM topics ORDER BY heat_score DESC, comments DESC LIMIT ?",
                (limit,))
        return [self._row("topics", r) for r in rows]

    def unused_topics(self, n: int = 10) -> list[Topic]:
        _, cols = _COLS["topics"]
        rows = self.conn.execute(
            f"SELECT {cols} FROM topics WHERE status='new' "
            "ORDER BY heat_score DESC, comments DESC LIMIT ?", (n,))
        return [self._row("topics", r) for r in rows]

    def get_topic(self, topic_id: int) -> Topic | None:
        return self._get("topics", "id=?", topic_id)

    def set_topic_status(self, topic_id: int, status: str) -> None:
        self.conn.execute("UPDATE topics SET status=? WHERE id=?", (status, topic_id))
        self.conn.commit()

    # ---- drafts ----
    def insert_draft(self, d: Draft) -> int:
        return self._insert("drafts", d)

    def get_draft(self, draft_id: int) -> Draft | None:
        return self._get("drafts", "id=?", draft_id)

    def list_drafts(self, limit: int = 100) -> list[Draft]:
        _, cols = _COLS["drafts"]
        rows = self.conn.execute(f"SELECT {cols} FROM drafts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row("drafts", r) for r in rows]

    def update_draft_title(self, draft_id: int, title: str) -> None:
        self.conn.execute("UPDATE drafts SET title=? WHERE id=?", (title, draft_id))
        self.conn.commit()

    # ---- videos ----
    def insert_video(self, v: Video) -> int:
        return self._insert("videos", v)

    def get_video(self, video_id: int) -> Video | None:
        return self._get("videos", "id=?", video_id)

    def videos_with_titles(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT v.*, d.title AS draft_title FROM videos v "
            "JOIN drafts d ON d.id=v.draft_id ORDER BY v.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---- posts ----
    def queue_post(self, video_id: int, account_id: int, scheduled_at: str) -> int:
        p = Post(video_id=video_id, account_id=account_id, scheduled_at=scheduled_at)
        self._insert("posts", p)
        return p.id

    def due_posts(self, now_iso: str) -> list[Post]:
        _, cols = _COLS["posts"]
        rows = self.conn.execute(
            f"SELECT {cols} FROM posts WHERE status='queued' AND scheduled_at<=? "
            "ORDER BY scheduled_at LIMIT 1", (now_iso,))
        return [self._row("posts", r) for r in rows]

    def list_posts(self, status: str | None = None, limit: int = 100) -> list[Post]:
        _, cols = _COLS["posts"]
        if status:
            rows = self.conn.execute(
                f"SELECT {cols} FROM posts WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit))
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM posts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row("posts", r) for r in rows]

    def update_post(self, p: Post) -> None:
        self.conn.execute(
            "UPDATE posts SET status=?, published_at=?, bjh_url=?, error=?, screenshot_path=? "
            "WHERE id=?",
            (p.status, p.published_at, p.bjh_url, p.error, p.screenshot_path, p.id))
        self.conn.commit()

    def published_today(self, account_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM posts WHERE account_id=? AND status='published' "
            "AND date(published_at)=date('now','localtime')", (account_id,)).fetchone()
        return row["c"]

    def stats(self) -> dict:
        tv = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(cost),0) s FROM videos").fetchone()
        tp = self.conn.execute(
            "SELECT COUNT(*) c FROM posts WHERE status='published' "
            "AND date(published_at)=date('now','localtime')").fetchone()
        return {"today_published": tp["c"], "total_videos": tv["c"], "total_cost": tv["s"]}
