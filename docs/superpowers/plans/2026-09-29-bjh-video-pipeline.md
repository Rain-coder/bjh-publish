# 百家号视频自动化流水线（bjh）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关键词 → 抓百家号热文 → DeepSeek 原创口播文案 → 本地合成竖屏口播视频 → 随机间隔自动发布，CLI + Web 面板 + Claude Code skill。

**Architecture:** 模块化 Python 单体（方案 A）。SQLite 存状态；Playwright 负责登录/抓取/发布；edge-tts + Pexels/Pillow + FFmpeg 本地合成视频；自研 asyncio 调度器带随机间隔与日上限；FastAPI + Alpine.js 面板。算法规则唯一沉淀在 `skills/bjh-video/SKILL.md` 的 `<rules>` 段，流水线运行时提取渲染 system prompt。

**Tech Stack:** Python 3.11+ · typer · playwright · httpx · edge-tts · Pillow · FFmpeg(subprocess) · beautifulsoup4 · SQLite · FastAPI · uvicorn · pytest(+asyncio, respx)

## Global Constraints

- Python ≥ 3.11；依赖仅限 Tech Stack 列表，不引入前端构建工具
- 系统需有 `ffmpeg`/`ffprobe`（macOS: `brew install ffmpeg`）；测试用 `shutil.which("ffmpeg")` 跳过无 ffmpeg 环境
- 单条视频现金成本 ≤ 0.3 元（edge-tts 0 元、Pexels 0 元、FFmpeg 本地 0 元、DeepSeek 计费写入 drafts.cost）
- 视频规格：1080×1920 竖屏 H.264+AAC MP4，成片目标 60~90s（测试样片可短）
- 发布默认：日上限 8 条、随机间隔 25~50 分钟、黄金时段 [7,9]/[12,14]/[18,22] 优先
- 内容必须原创生成（禁止搬运原文段落），发布前过敏感词过滤
- 数据一律落 `data/`（SQLite: `data/bjh.db`；视频: `data/videos/`；截图: `data/screenshots/`；账号 Cookie: `data/accounts/`）；`data/` 与 `config.toml` 进 .gitignore
- 每个任务以 conventional commit 收尾；测试命令统一 `python -m pytest <file> -v`（仓库根目录执行）
- 与规格一处差异：`published_today` 不作为 accounts 列，改为按 posts 表当日 published 计数（单一事实来源，行为等价）

---

### Task 1: 项目脚手架 + 配置加载

**Files:**
- Create: `pyproject.toml`、`.gitignore`、`config.example.toml`、`bjh/__init__.py`、`bjh/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `bjh.config.load_config(path: str | None = None) -> Config`，后续所有任务消费的字段：

```python
@dataclass
class Config:
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_price_in: float = 2.0   # 元/百万输入token（估算）
    deepseek_price_out: float = 8.0  # 元/百万输出token（估算）
    pexels_api_key: str = ""
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    daily_limit: int = 8
    interval_min_min: int = 25   # 发布随机间隔下限（分钟）
    interval_max_min: int = 50   # 发布随机间隔上限（分钟）
    golden_hours: list[list[int]] = field(default_factory=lambda: [[7, 9], [12, 14], [18, 22]])
    sensitive_words: list[str] = field(default_factory=lambda: ["震惊", "惊呆", "绝对", "100%", "必看", "删前速看", "转发过万"])
    data_dir: Path = Path("data")
```

- [ ] **Step 1: 写 pyproject / .gitignore / config.example.toml**

`pyproject.toml`：

```toml
[project]
name = "bjh"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "typer>=0.12",
  "playwright>=1.45",
  "httpx>=0.27",
  "edge-tts>=6.1",
  "pillow>=10.0",
  "beautifulsoup4>=4.12",
  "fastapi>=0.111",
  "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21"]

[project.scripts]
bjh = "bjh.cli:app"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`.gitignore`：

```
data/
config.toml
__pycache__/
*.pyc
.venv/
.pytest_cache/
```

`config.example.toml`（字段名与 Config 一一对应，kebab 不用，直接 snake_case）：

```toml
deepseek_api_key = ""
pexels_api_key = ""
tts_voice = "zh-CN-XiaoxiaoNeural"
daily_limit = 8
interval_min_min = 25
interval_max_min = 50
```

- [ ] **Step 2: 写失败测试 `tests/test_config.py`**

```python
from pathlib import Path
from bjh.config import load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 无 config.toml
    cfg = load_config(None)
    assert cfg.daily_limit == 8
    assert cfg.interval_min_min == 25 and cfg.interval_max_min == 50
    assert cfg.golden_hours == [[7, 9], [12, 14], [18, 22]]
    assert "震惊" in cfg.sensitive_words


def test_load_toml_and_env_override(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'deepseek_api_key = "toml-key"\ndaily_limit = 5\n', encoding="utf-8"
    )
    monkeypatch.setenv("BJH_DEEPSEEK_API_KEY", "env-key")
    cfg = load_config(str(tmp_path / "config.toml"))
    assert cfg.deepseek_api_key == "env-key"   # 环境变量优先
    assert cfg.daily_limit == 5                # toml 次之
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: bjh.config`）

- [ ] **Step 4: 实现 `bjh/config.py` + 空 `bjh/__init__.py`**

```python
# bjh/__init__.py
__version__ = "0.1.0"
```

```python
# bjh/config.py
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class Config:
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_price_in: float = 2.0
    deepseek_price_out: float = 8.0
    pexels_api_key: str = ""
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    daily_limit: int = 8
    interval_min_min: int = 25
    interval_max_min: int = 50
    golden_hours: list[list[int]] = field(default_factory=lambda: [[7, 9], [12, 14], [18, 22]])
    sensitive_words: list[str] = field(default_factory=lambda: [
        "震惊", "惊呆", "绝对", "100%", "必看", "删前速看", "转发过万"])
    data_dir: Path = Path("data")


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    p = Path(path) if path else Path(DEFAULT_CONFIG_NAME)
    if p.exists():
        with open(p, "rb") as f:
            data = tomllib.load(f)
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    env = {
        "deepseek_api_key": "BJH_DEEPSEEK_API_KEY",
        "pexels_api_key": "BJH_PEXELS_API_KEY",
    }
    for attr, var in env.items():
        if os.environ.get(var):
            setattr(cfg, attr, os.environ[var])
    return cfg
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 6: 安装依赖并提交**

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && python -m playwright install chromium
git add pyproject.toml .gitignore config.example.toml bjh/ tests/test_config.py
git commit -m "feat: 项目脚手架与配置加载"
```

---

### Task 2: 数据模型 + SQLite 访问层

**Files:**
- Create: `bjh/models.py`、`bjh/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces（后续所有任务消费）：dataclass `Account/Topic/Draft/Video/Post` 与 `class DB`：

```python
DB(path: Path)                     # __init__ 自动 init_schema
upsert_account(name: str, daily_limit: int = 8) -> Account
get_account(name: str) -> Account | None
get_account_by_id(account_id: int) -> Account | None
list_accounts() -> list[Account]
set_account_status(account_id: int, status: str) -> None      # active|expired
set_account_daily_limit(account_id: int, limit: int) -> None
insert_topic(t: Topic) -> int        # 回填 t.id
list_topics(keyword: str | None = None, limit: int = 100) -> list[Topic]
unused_topics(n: int = 10) -> list[Topic]   # status='new' 按 heat_score desc
set_topic_status(topic_id: int, status: str) -> None          # new|selected|skipped
insert_draft(d: Draft) -> int
get_draft(draft_id: int) -> Draft | None
list_drafts(limit: int = 100) -> list[Draft]
update_draft_title(draft_id: int, title: str) -> None
insert_video(v: Video) -> int
get_video(video_id: int) -> Video | None
videos_with_titles(limit: int = 100) -> list[dict]            # video 字段 + draft_title
queue_post(video_id: int, account_id: int, scheduled_at: str) -> int
due_posts(now_iso: str) -> list[Post]      # status='queued' 且 scheduled_at<=now，取最早1条
list_posts(status: str | None = None, limit: int = 100) -> list[Post]
update_post(p: Post) -> None
published_today(account_id: int) -> int    # 当日 status='published' 计数
stats() -> dict                            # {today_published, total_videos, total_cost}
```

- [ ] **Step 1: 写失败测试 `tests/test_db.py`**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL（ModuleNotFoundError: bjh.db）

- [ ] **Step 3: 实现 `bjh/models.py`**

```python
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
```

- [ ] **Step 4: 实现 `bjh/db.py`**

```python
# bjh/db.py
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
            import json
            d["tags"] = json.loads(d["tags"] or "[]")
        return cls(**d)

    def _insert(self, table: str, obj) -> int:
        _, cols = _COLS[table]
        names = [c for c in cols.split(",") if c != "id"]
        vals = []
        for n in names:
            v = getattr(obj, n)
            if isinstance(v, list):
                import json
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

    def get_account(self, name): return self._get("accounts", "name=?", name)
    def get_account_by_id(self, i): return self._get("accounts", "id=?", i)

    def list_accounts(self):
        _, cols = _COLS["accounts"]
        return [self._row("accounts", r) for r in
                self.conn.execute(f"SELECT {cols} FROM accounts ORDER BY id")]

    def set_account_status(self, account_id, status):
        self.conn.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))
        self.conn.commit()

    def set_account_daily_limit(self, account_id, limit):
        self.conn.execute("UPDATE accounts SET daily_limit=? WHERE id=?", (limit, account_id))
        self.conn.commit()

    # ---- topics ----
    def insert_topic(self, t: Topic) -> int: return self._insert("topics", t)

    def list_topics(self, keyword=None, limit=100):
        _, cols = _COLS["topics"]
        if keyword:
            rows = self.conn.execute(
                f"SELECT {cols} FROM topics WHERE keyword=? ORDER BY heat_score DESC LIMIT ?",
                (keyword, limit))
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM topics ORDER BY heat_score DESC LIMIT ?", (limit,))
        return [self._row("topics", r) for r in rows]

    def unused_topics(self, n=10):
        _, cols = _COLS["topics"]
        rows = self.conn.execute(
            f"SELECT {cols} FROM topics WHERE status='new' ORDER BY heat_score DESC LIMIT ?", (n,))
        return [self._row("topics", r) for r in rows]

    def set_topic_status(self, topic_id, status):
        self.conn.execute("UPDATE topics SET status=? WHERE id=?", (status, topic_id))
        self.conn.commit()

    # ---- drafts ----
    def insert_draft(self, d: Draft) -> int: return self._insert("drafts", d)
    def get_draft(self, i): return self._get("drafts", "id=?", i)

    def list_drafts(self, limit=100):
        _, cols = _COLS["drafts"]
        rows = self.conn.execute(f"SELECT {cols} FROM drafts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row("drafts", r) for r in rows]

    def update_draft_title(self, draft_id, title):
        self.conn.execute("UPDATE drafts SET title=? WHERE id=?", (title, draft_id))
        self.conn.commit()

    # ---- videos ----
    def insert_video(self, v: Video) -> int: return self._insert("videos", v)
    def get_video(self, i): return self._get("videos", "id=?", i)

    def videos_with_titles(self, limit=100):
        rows = self.conn.execute(
            "SELECT v.*, d.title AS draft_title FROM videos v "
            "JOIN drafts d ON d.id=v.draft_id ORDER BY v.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---- posts ----
    def queue_post(self, video_id, account_id, scheduled_at) -> int:
        p = Post(video_id=video_id, account_id=account_id, scheduled_at=scheduled_at)
        self._insert("posts", p)
        return p.id

    def due_posts(self, now_iso: str) -> list[Post]:
        _, cols = _COLS["posts"]
        rows = self.conn.execute(
            f"SELECT {cols} FROM posts WHERE status='queued' AND scheduled_at<=? "
            "ORDER BY scheduled_at LIMIT 1", (now_iso,))
        return [self._row("posts", r) for r in rows]

    def list_posts(self, status=None, limit=100):
        _, cols = _COLS["posts"]
        if status:
            rows = self.conn.execute(
                f"SELECT {cols} FROM posts WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit))
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM posts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row("posts", r) for r in rows]

    def update_post(self, p: Post):
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
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_db.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add bjh/models.py bjh/db.py tests/test_db.py
git commit -m "feat: 数据模型与SQLite访问层"
```

---

### Task 3: 选题打分与去重（writer/topic.py）

**Files:**
- Create: `bjh/writer/__init__.py`（空文件）、`bjh/writer/topic.py`
- Test: `tests/test_topic.py`

**Interfaces:**
- Consumes: `bjh.models.Topic`
- Produces:

```python
heat_score(reads: int, comments: int) -> float
title_bigrams(title: str) -> set[str]
jaccard(a: set[str], b: set[str]) -> float
dedupe_topics(topics: list[Topic], threshold: float = 0.55) -> list[Topic]  # 保热度高的
select_topics(topics: list[Topic], n: int = 1, used_titles: list[str] | None = None) -> list[Topic]
```

- [ ] **Step 1: 写失败测试 `tests/test_topic.py`**

```python
from bjh.models import Topic
from bjh.writer.topic import heat_score, title_bigrams, jaccard, dedupe_topics, select_topics


def T(title, reads, comments, heat=1.0):
    return Topic(keyword="kw", title=title, url="u", source_author="a",
                 reads=reads, comments=comments, heat_score=heat,
                 fetched_at="2026-09-29T10:00:00")


def test_heat_score_weights_comments():
    # 同阅读量下，评论多者显著更高；公式 = ln(reads+1) + 2.2*ln(comments+1)
    import math
    assert heat_score(10000, 10) == math.log(10001) + 2.2 * math.log(11)
    assert heat_score(10000, 500) > heat_score(10000, 10) + 3


def test_bigrams_and_jaccard():
    a = title_bigrams("月薪三万不敢生孩子")
    assert "月薪" in a and len(a) >= 6
    assert jaccard(title_bigrams("月薪三万不敢生孩子"), title_bigrams("月薪三万不敢生孩子")) == 1.0
    assert jaccard(title_bigrams("月薪三万"), title_bigrams("周末去哪玩")) == 0.0


def test_dedupe_keeps_higher_heat():
    t1 = T("月薪三万不敢生孩子的年轻人", 1000, 10, heat=5.0)
    t2 = T("月薪三万不敢生孩子？年轻人这样想", 1000, 10, heat=4.0)  # 相似标题
    t3 = T("完全不同的话题标题", 500, 5, heat=1.0)
    out = dedupe_topics([t2, t1, t3])           # t1 在前应保留 t1 去掉 t2
    titles = [t.title for t in out]
    assert "完全不同的话题标题" in titles
    assert titles.count("月薪三万不敢生孩子的年轻人") == 1
    dup = [t for t in out if "不敢生孩子" in t.title]
    assert len(dup) == 1 and dup[0].heat_score == 5.0


def test_select_topics_excludes_used():
    used = ["已经做过的标题"]
    t1 = T("已经做过的标题", 100, 900, heat=9.0)
    t2 = T("新选题标题", 100, 800, heat=8.0)
    out = select_topics([t1, t2], n=1, used_titles=used)
    assert [t.title for t in out] == ["新选题标题"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_topic.py -v`
Expected: FAIL（ModuleNotFoundError: bjh.writer.topic）

- [ ] **Step 3: 实现 `bjh/writer/topic.py`**

```python
# bjh/writer/topic.py
import math
from bjh.models import Topic


def heat_score(reads: int, comments: int) -> float:
    """评论权重 2.2 倍 —— 对齐"评论区互动"目标。"""
    return math.log(reads + 1) + 2.2 * math.log(comments + 1)


def title_bigrams(title: str) -> set[str]:
    s = "".join(c for c in title if c.isalnum())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe_topics(topics: list[Topic], threshold: float = 0.55) -> list[Topic]:
    """按热度降序遍历，与已保留标题相似度 >= threshold 的丢弃。"""
    kept: list[Topic] = []
    for t in sorted(topics, key=lambda x: x.heat_score, reverse=True):
        bi = title_bigrams(t.title)
        if any(jaccard(bi, title_bigrams(k.title)) >= threshold for k in kept):
            continue
        kept.append(t)
    return kept


def select_topics(topics: list[Topic], n: int = 1,
                  used_titles: list[str] | None = None) -> list[Topic]:
    used_titles = used_titles or []
    fresh = [t for t in topics if t.title not in used_titles]
    return dedupe_topics(fresh)[:n]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_topic.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/writer/ tests/test_topic.py
git commit -m "feat: 热度打分与标题去重选题"
```

---

### Task 4: 百家号算法 SKILL.md + prompt 构建/解析

**Files:**
- Create: `skills/bjh-video/SKILL.md`、`bjh/writer/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `bjh.models.Topic`
- Produces:

```python
extract_rules(skill_md: str) -> str                 # 提取 <rules>...</rules> 内容
build_system_prompt(rules: str) -> str
build_user_prompt(topic: Topic) -> str
parse_llm_json(raw: str) -> dict                    # {"titles":[...], "title":..., "hook":...,
                                                    #  "script":..., "closing_question":..., "tags":[...]}
pick_title(titles: list[str]) -> str                # 优先含"？"的悬念式
```

- [ ] **Step 1: 写 `skills/bjh-video/SKILL.md`**

````markdown
---
name: bjh-video
description: 百家号爆款视频流水线操作与算法规则。用户要选题/写百家号文案/生成/发布百家号视频时使用。可半自动逐步调用 bjh CLI，也可直接引用本文件 <rules> 作为创作规则。
---

# 百家号视频流水线 Skill

## 手动模式（Claude 逐步驱动）

1. `bjh fetch --keyword <关键词> --pages 1` 抓热文（含阅读/评论数）
2. `bjh create --count 1 [--topic-id ID]` 生成文案与视频（人可在 drafts 表审标题）
3. 审查通过后 `bjh publish --dry-run` 预演，再 `bjh publish --now` 真发
4. 全自动：`bjh run --keyword <关键词>`；常驻调度：`bjh publish --daemon`

创作/评估任何一条内容前，必须逐条对照下方 <rules>。

<rules>
## 选题规则
- 评论比 = 评论数/阅读量 > 1% 的话题优先（互动潜力高）
- 优先争议性、身份代入感强（家长/职场/收入/情感两难）话题
- 热点时效窗口 ≤ 48 小时；过期热点改用角度切入而非复述事件
- 同一话题只做一个视频，避免同质化判定

## 标题规则
- ≤28 字；三模板择一：悬念式（留问号）、数字式（含具体数字）、身份代入式（目标人群开头）
- 禁用夸张违禁词：震惊/惊呆/绝对/100%/必看/删前速看/转发过万
- 标题须与结尾提问呼应

## 文案结构（60~90 秒口播稿，180~260 字）
1. 3 秒钩子：反常识断言或两难提问，直接点出目标人群
2. 冲突递进：2~3 个转折，每 15~20 秒一个信息点
3. 口语化短句：每句 ≤20 字，禁书面语
4. 结尾开放式提问：给出 A/B 两个立场让用户站队，禁止自问自答
5. 不下结论、不说教；信息留缺口驱动评论补充

## 视频规范
- 9:16 竖屏 1080×1920，60~90 秒
- 硬字幕：大字（约屏宽 7% 字号）、白字黑描边、水平居中、位于下三分之一
- 前 3 秒画面出现标题关键词大字
- 封面三要素：大字标题、主体视觉、高对比色

## 发布节奏
- 黄金时段：7-9 / 12-14 / 18-22 点
- 单账号日上限 ≤ 8 条，间隔 ≥ 25 分钟且随机
- 避坑：不搬运原文段落（必须原创重写）、不发模板化同质文、敏感词过滤后再发布
</rules>

## 输出 JSON 契约（自动化流水线）

```json
{"titles": ["候选1", "候选2", "候选3"], "title": "最终标题",
 "hook": "开头钩子", "script": "完整口播稿", "closing_question": "结尾提问", "tags": ["标签1", "标签2"]}
```
````

- [ ] **Step 2: 写失败测试 `tests/test_prompts.py`**

```python
from bjh.models import Topic
from bjh.writer.prompts import extract_rules, build_system_prompt, build_user_prompt, parse_llm_json, pick_title

SKILL = "前文\n<rules>\n规则A\n规则B\n</rules>\n后文"
T = Topic(keyword="职场", title="月薪三万不敢生孩子", url="u", source_author="a",
          reads=100000, comments=2300, heat_score=1.0, fetched_at="2026-09-29T10:00:00")


def test_extract_rules():
    assert "规则A" in extract_rules(SKILL) and "后文" not in extract_rules(SKILL)


def test_build_prompts_contain_rules_and_data():
    sp = build_system_prompt(extract_rules(SKILL))
    assert "规则A" in sp and "JSON" in sp
    up = build_user_prompt(T)
    assert "月薪三万不敢生孩子" in up and "100000" in up and "2300" in up


def test_parse_llm_json_strips_fence_and_validates():
    good = '```json\n{"titles": ["a？", "b"], "title": "a？", "hook": "h", "script": "口" * 200, "closing_question": "q", "tags": ["t"]}\n```'
    d = parse_llm_json(good)
    assert d["title"] == "a？" and len(d["tags"]) == 1


def test_parse_llm_json_rejects_bad():
    import pytest
    with pytest.raises(ValueError):
        parse_llm_json('{"titles": ["只有标题"]}')          # 缺字段
    with pytest.raises(ValueError):
        parse_llm_json('```json\n{"titles": ["a"] * 3, "title": "a", "hook": "h", "script": "太短", "closing_question": "q", "tags": []}\n```')


def test_pick_title_prefers_question():
    assert pick_title(["数字式标题", "悬念式标题？", "代入式标题"]) == "悬念式标题？"
    assert pick_title(["只有一个"]) == "只有一个"
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL（ModuleNotFoundError: bjh.writer.prompts）

- [ ] **Step 4: 实现 `bjh/writer/prompts.py`**

```python
# bjh/writer/prompts.py
import json
import re
from bjh.models import Topic

SKILL_PATH_DEFAULT = "skills/bjh-video/SKILL.md"
REQUIRED_KEYS = ("titles", "title", "hook", "script", "closing_question", "tags")
SCRIPT_MIN, SCRIPT_MAX = 150, 320   # 字数契约（60~90s 口播）
TITLE_MAX = 28


def extract_rules(skill_md: str) -> str:
    m = re.search(r"<rules>(.*?)</rules>", skill_md, re.S)
    if not m:
        raise ValueError("SKILL.md 缺少 <rules> 段")
    return m.group(1).strip()


def build_system_prompt(rules: str) -> str:
    return (
        "你是百家号爆款口播视频策划。严格遵循以下平台算法规则创作：\n\n"
        f"{rules}\n\n"
        "输出必须是合法 JSON（不要输出其他内容），字段：titles(3个候选), title(最终), "
        "hook, script(180~260字口播稿), closing_question, tags(2~4个，不含#)。"
        f"约束：title ≤ {TITLE_MAX} 字；script 每句 ≤20 字且口语化；"
        "结尾提问必须是 A/B 站队式开放式问题。"
    )


def build_user_prompt(topic: Topic) -> str:
    ratio = (topic.comments / topic.reads * 100) if topic.reads else 0.0
    return (
        f"参考热门文章（禁止搬运原句，必须原创重写）：\n"
        f"标题：{topic.title}\n阅读 {topic.reads}，评论 {topic.comments}（评论比 {ratio:.2f}%）\n"
        f"关键词：{topic.keyword}\n"
        "请按规则围绕该话题写一条全新角度的口播视频文案。"
    )


def parse_llm_json(raw: str) -> dict:
    text = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出非合法 JSON: {e}") from e
    for k in REQUIRED_KEYS:
        if k not in d:
            raise ValueError(f"LLM 输出缺少字段 {k}")
    if not isinstance(d["titles"], list) or len(d["titles"]) < 3:
        raise ValueError("titles 必须是 3 个候选")
    if not (SCRIPT_MIN <= len(d["script"]) <= SCRIPT_MAX):
        raise ValueError(f"script 字数 {len(d['script'])} 不在 {SCRIPT_MIN}~{SCRIPT_MAX}")
    if len(d["title"]) > TITLE_MAX:
        raise ValueError(f"title 超过 {TITLE_MAX} 字")
    return d


def pick_title(titles: list[str]) -> str:
    for t in titles:                       # 悬念式优先（含问号）
        if "？" in t or "?" in t:
            return t
    return titles[0]
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: 5 PASS

- [ ] **Step 5b: 验证 SKILL.md 规则可被提取（契约测试）**

在 `tests/test_prompts.py` 末尾追加：

```python
def test_real_skill_file_rules_extractable():
    from pathlib import Path
    from bjh.writer.prompts import SKILL_PATH_DEFAULT
    p = Path(SKILL_PATH_DEFAULT)
    if not p.exists():            # 从仓库根跑测试
        p = Path(__file__).resolve().parents[2] / SKILL_PATH_DEFAULT
    rules = extract_rules(p.read_text(encoding="utf-8"))
    for key in ["选题规则", "标题规则", "文案结构", "视频规范", "发布节奏"]:
        assert key in rules
```

Run: `python -m pytest tests/test_prompts.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add skills/bjh-video/SKILL.md bjh/writer/prompts.py tests/test_prompts.py
git commit -m "feat: 百家号算法skill与prompt构建解析"
```

---

### Task 5: DeepSeek 客户端（mock 测试）

**Files:**
- Create: `bjh/writer/deepseek.py`
- Test: `tests/test_deepseek.py`

**Interfaces:**
- Consumes: `Config.deepseek_api_key / deepseek_base_url / deepseek_model`
- Produces:

```python
class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", timeout: float = 60.0)
    def chat(self, messages: list[dict], json_mode: bool = False, retries: int = 2)
        -> tuple[str, dict]     # (content, usage)  usage 含 prompt_tokens/completion_tokens
def estimate_cost(usage: dict, cfg) -> float   # 元
```

- [ ] **Step 1: 写失败测试 `tests/test_deepseek.py`**

```python
import httpx
import pytest
import respx
from bjh.config import Config
from bjh.writer.deepseek import DeepSeekClient, estimate_cost

MSG = [{"role": "user", "content": "hi"}]


def _mock_response(respx_mock, status=200, content="ok", n=1):
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        respx.Response(status, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100 * n, "completion_tokens": 200},
        }))
    return route


@respx.mock
def test_chat_success(respx_mock):
    route = _mock_response(respx_mock, content="回答")
    c = DeepSeekClient("k")
    text, usage = c.chat(MSG)
    assert text == "回答" and usage["completion_tokens"] == 200
    body = route.calls.last.request.content
    assert b'"model":"deepseek-chat"' in body


@respx.mock
def test_chat_retries_on_500(respx_mock):
    route = _mock_response(respx_mock, status=500)
    route.side_effect = [
        httpx.Response(500, json={}),
        httpx.Response(200, json={"choices": [{"message": {"content": "重试成功"}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    ]
    c = DeepSeekClient("k")
    text, _ = c.chat(MSG)
    assert text == "重试成功" and route.call_count == 2


@respx.mock
def test_chat_raises_after_retries(respx_mock):
    _mock_response(respx_mock, status=500)
    c = DeepSeekClient("k")
    with pytest.raises(httpx.HTTPError):
        c.chat(MSG, retries=1)


@respx.mock
def test_json_mode_flag(respx_mock):
    route = _mock_response(respx_mock)
    DeepSeekClient("k").chat(MSG, json_mode=True)
    assert b'"response_format":{"type":"json_object"}' in route.calls.last.request.content


def test_estimate_cost():
    cfg = Config()   # 2.0 / 8.0 元每百万 token
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    assert abs(estimate_cost(usage, cfg) - (1000 * 2.0 + 500 * 8.0) / 1e6) < 1e-12
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_deepseek.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `bjh/writer/deepseek.py`**

```python
# bjh/writer/deepseek.py
import time
import httpx
from bjh.config import Config


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", timeout: float = 60.0):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"})

    def chat(self, messages: list[dict], json_mode: bool = False,
             retries: int = 2) -> tuple[str, dict]:
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.8}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._client.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"], data.get("usage", {})
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last_exc = e
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt)
        raise last_exc  # pragma: no cover


def estimate_cost(usage: dict, cfg: Config) -> float:
    return (usage.get("prompt_tokens", 0) * cfg.deepseek_price_in
            + usage.get("completion_tokens", 0) * cfg.deepseek_price_out) / 1e6
```

注意：respx 对 side_effect 列表逐个返回；`test_chat_retries_on_500` 里第一次 mock 响应被 side_effect 覆盖。若断言失败，检查 respx 版本行为并调整 mock 写法（保持语义：500 后重试成功）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_deepseek.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/writer/deepseek.py tests/test_deepseek.py
git commit -m "feat: DeepSeek客户端(重试+成本估算)"
```

---

### Task 6: TTS 封装 + 句子时间轴（纯逻辑测试）

**Files:**
- Create: `bjh/studio/__init__.py`（空文件）、`bjh/studio/tts.py`
- Test: `tests/test_tts.py`

**Interfaces:**
- Produces:

```python
Word = namedtuple("Word", ["offset_sec", "text"])
Sentence = namedtuple("Sentence", ["text", "start_sec", "end_sec"])
split_sentences(text: str) -> list[str]                       # 按 。！？!?… 切
char_len(s: str) -> int                                       # 去标点空白后长度
sentences_with_timing(words: list[Word], sentences: list[str]) -> list[Sentence]
async def synthesize(text: str, voice: str, out_mp3: Path) -> list[Word]   # edge-tts，网络
async def synthesize_with_retry(text, voice, out_mp3, retries=3) -> list[Word]  # 指数退避(1s/2s)
```

- [ ] **Step 1: 写失败测试 `tests/test_tts.py`**

```python
import pytest
from bjh.studio.tts import Word, split_sentences, char_len, sentences_with_timing


def test_split_sentences():
    text = "月薪三万够不够花？很多人说不够。但真相是什么！你绝对想不到…"
    parts = split_sentences(text)
    assert len(parts) == 4 and parts[0].endswith("？")


def test_char_len_ignores_punct():
    assert char_len("你好，世界。") == 4


def test_sentences_with_timing_aligns_by_charcount():
    sents = ["第一句话共六字", "第二句短些"]
    # 8 个 word，每词 3 字（char_len=3），offset 从 0.0 起步长 0.5s
    words = [Word(0.0 + i * 0.5, f"词{i}番") for i in range(8)]
    out = sentences_with_timing(words, sents)
    assert out[0].text == "第一句话共六字"
    assert out[0].start_sec == 0.0
    # 第一句 need=7：消耗 3 个词(got=9) → 结束于下一词 offset=1.5
    assert out[0].end_sec == pytest.approx(1.5)
    assert out[1].start_sec == 1.5
    # 无论词是否用尽，句尾都保证 ≥ start+0.3
    assert out[-1].end_sec >= out[-1].start_sec + 0.3
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tts.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `bjh/studio/tts.py`**

```python
# bjh/studio/tts.py
import asyncio
import re
from collections import namedtuple
from pathlib import Path

import edge_tts

Word = namedtuple("Word", ["offset_sec", "text"])
Sentence = namedtuple("Sentence", ["text", "start_sec", "end_sec"])
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])")


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]


def char_len(s: str) -> int:
    return len(re.sub(r"[\s，。！？!?…、：；''""“”‘’]", "", s))


def sentences_with_timing(words: list[Word], sentences: list[str]) -> list[Sentence]:
    result: list[Sentence] = []
    wi = 0
    prev_end = words[0].offset_sec if words else 0.0
    for sent in sentences:
        start = prev_end
        need, got = char_len(sent), 0
        while wi < len(words) and got < need:
            got += char_len(words[wi].text)
            wi += 1
        if wi < len(words):
            end = words[wi].offset_sec
        elif words:
            end = words[-1].offset_sec + 0.5
        else:
            end = start + 1.0
        end = max(end, start + 0.3)
        result.append(Sentence(sent, start, end))
        prev_end = end
    return result


async def synthesize(text: str, voice: str, out_mp3: Path) -> list[Word]:
    """edge-tts 合成，返回词级时间轴（offset 秒）。"""
    words: list[Word] = []
    comm = edge_tts.Communicate(text, voice)
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(Word(chunk["offset"] / 1e7, chunk["text"]))
    return words


async def synthesize_with_retry(text: str, voice: str, out_mp3: Path, retries: int = 3) -> list[Word]:
    for attempt in range(retries):
        try:
            return await synthesize(text, voice, out_mp3)
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("unreachable")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tts.py -v`
Expected: 3 PASS

- [ ] **Step 5: （手动冒烟，不进 CI）真实合成 3 秒音频**

```bash
python - <<'PY'
import asyncio
from pathlib import Path
from bjh.studio.tts import synthesize
ws = asyncio.run(synthesize("这是一条测试音频。", "zh-CN-XiaoxiaoNeural", Path("data/tts_test.mp3")))
print("words:", len(ws), ws[:3])
PY
```
Expected: 生成 `data/tts_test.mp3`（可播放），打印词时间轴。失败则检查 edge-tts 版本 API。

- [ ] **Step 6: Commit**

```bash
git add bjh/studio/ tests/test_tts.py
git commit -m "feat: edge-tts封装与句子时间轴对齐"
```

---

### Task 7: ASS 硬字幕生成

**Files:**
- Create: `bjh/studio/subtitle.py`
- Test: `tests/test_subtitle.py`

**Interfaces:**
- Consumes: `bjh.studio.tts.Sentence`
- Produces:

```python
wrap_line(text: str, max_chars: int = 15) -> list[str]   # 每行≤max_chars，最多2行（超出截断加…）
ass_time(sec: float) -> str                              # "0:00:01.50"
build_ass(sentences: list[Sentence], font_size: int = 76,
          width: int = 1080, height: int = 1920, font: str = "PingFang SC") -> str
```

- [ ] **Step 1: 写失败测试 `tests/test_subtitle.py`**

```python
from bjh.studio.tts import Sentence
from bjh.studio.subtitle import wrap_line, ass_time, build_ass


def test_wrap_line():
    assert wrap_line("短句", 15) == ["短句"]
    long = "这一句话特别长需要被切成两行显示才不会超出屏幕"
    lines = wrap_line(long, 15)
    assert len(lines) == 2 and all(len(l) <= 15 for l in lines)


def test_ass_time():
    assert ass_time(1.5) == "0:00:01.50"
    assert ass_time(3661.25) == "1:01:01.25"


def test_build_ass_structure():
    sents = [Sentence("第一句", 0.0, 2.0), Sentence("第二句超长超长超长超长超长超长超长超长", 2.0, 5.0)]
    ass = build_ass(sents)
    assert "[Script Info]" in ass and "1080" in ass and "1920" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,第一句" in ass
    assert "\\N" in ass          # 长句换行
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_subtitle.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `bjh/studio/subtitle.py`**

```python
# bjh/studio/subtitle.py
import re
from bjh.studio.tts import Sentence

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fs},&H00FFFFFF,&H00000000,1,4,1,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Text
"""


def wrap_line(text: str, max_chars: int = 15) -> list[str]:
    text = re.sub(r"\s+", "", text)
    lines = [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1][:max_chars - 1] + "…"
    return lines


def ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(sentences: list[Sentence], font_size: int = 76, width: int = 1080,
              height: int = 1920, font: str = "PingFang SC") -> str:
    out = [ASS_HEADER.format(width=width, height=height, font=font, fs=font_size)]
    for s in sentences:
        text = "\\N".join(wrap_line(s.text))
        out.append(f"Dialogue: 0,{ass_time(s.start_sec)},{ass_time(s.end_sec)},Default,{text}")
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_subtitle.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/studio/subtitle.py tests/test_subtitle.py
git commit -m "feat: ASS硬字幕生成"
```

---

### Task 8: 配图（Pexels + Pillow 兜底）

**Files:**
- Create: `bjh/studio/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Produces:

```python
find_font() -> str                        # 系统中文字体路径，找不到抛 FileNotFoundError
make_fallback_image(title: str, out_path: Path, palette_index: int = 0) -> Path
async def fetch_pexels(query: str, count: int, out_dir: Path, api_key: str | None) -> list[Path]
async def collect_images(query: str, count: int, out_dir: Path,
                         api_key: str | None, titles: list[str]) -> list[Path]
                         # Pexels 优先；不足部分用 Pillow 模板图补齐，保证 len == count
```

- [ ] **Step 1: 写失败测试 `tests/test_images.py`**

```python
from pathlib import Path
import pytest
from PIL import Image
import respx
from bjh.studio.images import make_fallback_image, collect_images, find_font

# 兜底图需要系统中文字体；无字体环境（部分 CI）跳过
try:
    find_font()
    HAS_FONT = True
except FileNotFoundError:
    HAS_FONT = False
needs_font = pytest.mark.skipif(not HAS_FONT, reason="无系统中文字体")


@needs_font
def test_fallback_image_generated(tmp_path):
    out = tmp_path / "bg0.png"
    make_fallback_image("测试标题", out, palette_index=1)
    img = Image.open(out)
    assert img.size == (1080, 1920)


def test_find_font_returns_existing_path():
    if not HAS_FONT:
        pytest.skip("no CJK font")
    assert Path(find_font()).exists()


@respx.mock
@needs_font
async def test_collect_images_falls_back_when_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("BJH_PEXELS_API_KEY", raising=False)
    paths = await collect_images("职场", 3, tmp_path, None, titles=["标题A", "标题B", "标题C"])
    assert len(paths) == 3 and all(p.exists() for p in paths)


@respx.mock
async def test_fetch_pexels_downloads(tmp_path, respx_mock):
    respx_mock.get("https://api.pexels.com/v1/search").mock(respx.Response(200, json={
        "photos": [{"src": {"portrait": "https://img.example/1.jpg"}}]}))
    respx_mock.get("https://img.example/1.jpg").mock(
        respx.Response(200, content=b"\xff\xd8fakejpg"))
    from bjh.studio.images import fetch_pexels
    paths = await fetch_pexels("职场", 1, tmp_path, "fake-key")
    assert len(paths) == 1 and paths[0].read_bytes().startswith(b"\xff\xd8")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_images.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `bjh/studio/images.py`**

```python
# bjh/studio/images.py
import httpx
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
PALETTES = [((24, 30, 60), (90, 60, 160)), ((10, 60, 70), (20, 140, 130)),
            ((70, 20, 40), (170, 60, 90))]
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise FileNotFoundError("未找到系统中文字体，请安装 PingFang/黑体/Noto CJK")


def make_fallback_image(title: str, out_path: Path, palette_index: int = 0) -> Path:
    c1, c2 = PALETTES[palette_index % len(PALETTES)]
    img = Image.new("RGB", (W, H))
    for y in range(H):                      # 垂直渐变
        t = y / H
        img.paste(tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)), (0, y, W, y + 1))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(), 110)
    # 标题每行6字，居中大字
    line = title[:12]
    lines = [line[i:i + 6] for i in range(0, len(line), 6)]
    total_h = len(lines) * 150
    y0 = (H - total_h) // 2
    for i, ln in enumerate(lines):
        bbox = d.textbbox((0, 0), ln, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        d.text((x + 6, y0 + i * 150 + 6), ln, font=font, fill=(0, 0, 0))
        d.text((x, y0 + i * 150), ln, font=font, fill=(255, 255, 255))
    img.save(out_path)
    return out_path


async def fetch_pexels(query: str, count: int, out_dir: Path, api_key: str | None) -> list[Path]:
    if not api_key:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": api_key},
                             params={"query": query, "per_page": count, "orientation": "portrait"})
        r.raise_for_status()
        photos = r.json().get("photos", [])
        paths = []
        for i, ph in enumerate(photos[:count]):
            url = ph["src"]["portrait"]
            data = (await client.get(url)).content
            p = out_dir / f"pexels_{i}.jpg"
            p.write_bytes(data)
            paths.append(p)
        return paths


async def collect_images(query: str, count: int, out_dir: Path,
                         api_key: str | None, titles: list[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        paths = await fetch_pexels(query, count, out_dir, api_key)
    except Exception:
        paths = []
    for i in range(count - len(paths)):
        title = titles[i % len(titles)] if titles else query
        paths.append(make_fallback_image(title, out_dir / f"fallback_{i}.png", palette_index=i))
    return paths[:count]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_images.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/studio/images.py tests/test_images.py
git commit -m "feat: Pexels配图与Pillow模板兜底"
```

---

### Task 9: FFmpeg 合成（含 5s 样片集成测试）

**Files:**
- Create: `bjh/studio/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Produces:

```python
def audio_duration(path: Path) -> float                     # ffprobe
def compose_video(audio_path: Path, image_paths: list[Path], ass_path: Path | None,
                  out_path: Path, fps: int = 30) -> Path    # Ken Burns 缓放 + 字幕烧录 + 音频
```

- [ ] **Step 1: 写失败测试 `tests/test_compose.py`**

```python
import shutil
import subprocess
from pathlib import Path
import pytest
from bjh.studio.tts import Sentence
from bjh.studio.subtitle import build_ass
from bjh.studio.images import make_fallback_image
from bjh.studio.compose import audio_duration, compose_video

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not ffmpeg or not shutil.which("ffprobe"),
                                reason="需要系统 ffmpeg/ffprobe")


def make_sine_mp3(path: Path, seconds: float = 3.0):
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)], check=True, capture_output=True)


def test_audio_duration(tmp_path):
    mp3 = tmp_path / "a.mp3"
    make_sine_mp3(mp3, 3.0)
    assert 2.8 < audio_duration(mp3) < 3.3


def test_compose_5s_sample(tmp_path):
    mp3 = tmp_path / "a.mp3"
    make_sine_mp3(mp3, 3.0)
    imgs = [make_fallback_image(f"画面{i}", tmp_path / f"img{i}.png", palette_index=i)
            for i in range(2)]
    sents = [Sentence("第一句字幕", 0.0, 1.5), Sentence("第二句字幕", 1.5, 3.0)]
    ass = tmp_path / "sub.ass"
    ass.write_text(build_ass(sents), encoding="utf-8")
    out = tmp_path / "out.mp4"
    compose_video(mp3, imgs, ass, out)
    assert out.exists() and out.stat().st_size > 10000
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "1080,1920"
    assert 2.8 < audio_duration(out) < 3.3
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_compose.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `bjh/studio/compose.py`**

```python
# bjh/studio/compose.py
import subprocess
from pathlib import Path

W, H = 1080, 1920


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def compose_video(audio_path: Path, image_paths: list[Path], ass_path: Path | None,
                  out_path: Path, fps: int = 30) -> Path:
    """每张图占 audio_duration/len(images) 秒，zoompan Ken Burns 缓放，concat 后烧录 ASS 字幕。"""
    assert image_paths, "至少需要一张图"
    dur = audio_duration(audio_path)
    seg = dur / len(image_paths)
    cmd: list[str] = ["ffmpeg", "-y"]
    for img in image_paths:
        cmd += ["-loop", "1", "-t", f"{seg:.3f}", "-i", str(img)]
    cmd += ["-i", str(audio_path)]
    n = len(image_paths)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"zoompan=z='min(zoom+0.0006,1.2)':d={int(seg * fps)}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},setsar=1[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(n))
    chain = ";".join(parts) + f";{concat_in}concat=n={n}:v=1:a=0[v]"
    vmap = "[v]"
    if ass_path:
        # ass 文件名必须无特殊字符（调用方保证落在 data/videos/work 下）
        chain += f";[v]subtitles={ass_path}[vout]"
        vmap = "[vout]"
    cmd += ["-filter_complex", chain, "-map", vmap, "-map", f"{n}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-shortest", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{proc.stderr[-2000:]}")
    return out_path
```

注意：`subtitles=` 滤镜的路径若含 `:` 或空格需转义；实现时把 ass 复制为 `out_path.parent/ subs.ass` 简单名。若集成测试因路径转义失败，在 compose_video 开头加：`safe_ass = out_path.parent / "subs.ass"; shutil.copy(ass_path, safe_ass)` 并用 `safe_ass`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_compose.py -v`
Expected: 2 PASS（无 ffmpeg 环境为 SKIP）

- [ ] **Step 5: Commit**

```bash
git add bjh/studio/compose.py tests/test_compose.py
git commit -m "feat: FFmpeg Ken Burns合成竖屏视频"
```

---

### Task 10: 百家号抓取（解析器 TDD + 浏览器流程）

**Files:**
- Create: `bjh/fetcher/__init__.py`（空文件）、`bjh/fetcher/baijiahao.py`
- Test: `tests/test_fetcher_parse.py`

**Interfaces:**
- Consumes: `bjh.writer.topic.heat_score`、`bjh.db.DB`
- Produces:

```python
TopicRaw = namedtuple("TopicRaw", ["title", "url", "author"])
parse_baidu_serp(html: str, keyword: str) -> list[TopicRaw]      # 过滤出百家号链接
parse_article_stats(html: str) -> dict                           # {"reads": int, "comments": int}
async def login(account_name: str, cfg) -> Account               # 扫码登录存 storage_state
async def fetch_topics(keyword: str, pages: int, account: Account,
                       headless: bool = True) -> list[Topic]     # 组合上述，含 heat_score
```

- [ ] **Step 1: 写失败测试 `tests/test_fetcher_parse.py`**

```python
from bjh.fetcher.baijiahao import parse_baidu_serp, parse_article_stats

SERP = """
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=1">月薪三万却不敢生孩子的年轻人_百家号</a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://www.example.com/news/1"> unrelated 普通网站文章 </a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=2">职场焦虑真相大揭秘_百家号</a></h3>
</div>
<div class="result c-container">
  <h3><a href="https://baijiahao.baidu.com/s?id=1">月薪三万却不敢生孩子的年轻人_百家号</a></h3>
</div>
"""

ARTICLE = """
<script>window._show = {"readCount": "12345"}</script>
<div>评论 234</div>
"""


def test_parse_baidu_serp_filters_and_dedupes():
    out = parse_baidu_serp(SERP, "月薪")
    urls = [t.url for t in out]
    assert urls == ["https://baijiahao.baidu.com/s?id=1", "https://baijiahao.baidu.com/s?id=2"]
    assert out[0].title == "月薪三万却不敢生孩子的年轻人"


def test_parse_article_stats_multi_pattern():
    stats = parse_article_stats(ARTICLE)
    assert stats == {"reads": 12345, "comments": 234}
    assert parse_article_stats('{"read_count": 99, "commentCount": 8}') == {"reads": 99, "comments": 8}
    assert parse_article_stats("什么都没有") == {"reads": 0, "comments": 0}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_fetcher_parse.py -v`
Expected: FAIL

- [ ] **Step 3: 实现解析函数 + 浏览器流程 `bjh/fetcher/baijiahao.py`**

```python
# bjh/fetcher/baijiahao.py
import asyncio
import re
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from bjh.config import Config
from bjh.models import Account, Topic
from bjh.writer.topic import heat_score

TopicRaw = namedtuple("TopicRaw", ["title", "url", "author"])

# ==== 选择器/URL 常量（真实站点变动时只需改这里；Task 10 Step 6 冒烟核对）====
BAIDU_SERP_URL = "https://www.baidu.com/s?wd={kw}&rn=20"
BJH_LINK_MARK = "baijiahao.baidu.com/s?id="
TITLE_SUFFIX = re.compile(r"[_\-|·]\s*百家号\s*$")

READ_PATTERNS = [re.compile(r'"readCount"\s*:\s*"?(\d+)'), re.compile(r'"read_count"\s*:\s*"?(\d+)'),
                 re.compile(r"阅\s*读[量数]?\s*[：:相]?\s*(\d+)")]
COMMENT_PATTERNS = [re.compile(r'"commentCount"\s*:\s*"?(\d+)'), re.compile(r"评\s*论[数量]?\s*[：:]?\s*(\d+)")]


def _first_int(html: str, patterns) -> int:
    for p in patterns:
        m = p.search(html)
        if m:
            return int(m.group(1))
    return 0


def parse_baidu_serp(html: str, keyword: str) -> list[TopicRaw]:
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").strip()
        if BJH_LINK_MARK not in href or len(text) < 8:
            continue
        title = TITLE_SUFFIX.sub("", text).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(TopicRaw(title=title, url=href, author=""))
    return out


def parse_article_stats(html: str) -> dict:
    return {"reads": _first_int(html, READ_PATTERNS), "comments": _first_int(html, COMMENT_PATTERNS)}


async def login(account_name: str, cfg: Config) -> Account:
    """打开浏览器等用户扫码，保存 storage_state。"""
    state_path = Path(cfg.data_dir) / "accounts" / f"{account_name}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://baijiahao.baidu.com")
        input(f"在浏览器中完成登录（账号:{account_name}），登录成功回到首页后按回车保存 Cookie...")
        await ctx.storage_state(path=str(state_path))
        await browser.close()
    from bjh.db import DB
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    db = DB(Path(cfg.data_dir) / "bjh.db")
    return db.upsert_account(account_name)


async def fetch_topics(keyword: str, pages: int, account: Account,
                       headless: bool = True) -> list[Topic]:
    """百度 SERP 过滤百家号结果 → 逐篇打开取阅读/评论 → 组装 Topic。"""
    state = Path(account.storage_state_path)
    if not state.exists():
        raise FileNotFoundError(f"账号 {account.name} 无登录态，请先 bjh login")
    topics: list[Topic] = []
    now = datetime.now().isoformat(timespec="seconds")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(storage_state=str(state))
        page = await ctx.new_page()
        for pn in range(pages):
            await page.goto(BAIDU_SERP_URL.format(kw=keyword) + (f"&pn={pn * 20}" if pn else ""))
            await page.wait_for_timeout(1500)
            raws = parse_baidu_serp(await page.content(), keyword)
            for r in raws[:10]:
                try:
                    art = await ctx.new_page()
                    await art.goto(r.url, timeout=20000)
                    await art.wait_for_timeout(800)
                    stats = parse_article_stats(await art.content())
                    await art.close()
                except Exception:
                    stats = {"reads": 0, "comments": 0}
                topics.append(Topic(keyword=keyword, title=r.title, url=r.url,
                                    source_author=r.author, reads=stats["reads"],
                                    comments=stats["comments"],
                                    heat_score=heat_score(stats["reads"], stats["comments"]),
                                    fetched_at=now))
        await browser.close()
    return topics
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_fetcher_parse.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/fetcher/ tests/test_fetcher_parse.py
git commit -m "feat: 百家号热文抓取(SERP解析+浏览器流程)"
```

- [ ] **Step 6: （手动冒烟，真账号，不进 CI）**

```bash
bjh login            # 扫码
python - <<'PY'
import asyncio
from pathlib import Path
from bjh.config import load_config
from bjh.db import DB
from bjh.fetcher.baijiahao import fetch_topics
cfg = load_config()
db = DB(Path("data/bjh.db"))
acc = db.get_account("default")
ts = asyncio.run(fetch_topics("职场焦虑", 1, acc, headless=False))
for t in ts[:5]:
    print(f"{t.heat_score:8.2f}  R{t.reads:>8} C{t.comments:>6}  {t.title}")
    db.insert_topic(t)
PY
```
Expected: 打印热文与热度分并入库。**若解析数量为 0**：把 `page.content()` 存文件检查 DOM，仅调整模块顶部常量（`BJH_LINK_MARK`/正则），不改函数结构，直到抓到数据；把真实 DOM 片段补充为 `tests/test_fetcher_parse.py` 的新 fixture 用例后重新提交。

---

### Task 11: 百家号自动发布（Playwright，dry-run 优先）

**Files:**
- Create: `bjh/publisher/__init__.py`（空文件）、`bjh/publisher/baijiahao.py`
- Test: 无单元测试（浏览器自动化，靠 Task 16 手动冒烟）——本任务交付代码 + 选择器常量文档

**Interfaces:**
- Consumes: `Account.storage_state_path`、`Draft.title/tags`、`Video.path`
- Produces:

```python
async def publish_video(video_path: Path, title: str, tags: list[str],
                        state_path: Path, shot_dir: Path,
                        dry_run: bool = False) -> str
# 返回百家号文章 URL；dry_run 返回 "DRY-RUN-OK"。任何步骤失败截图到 shot_dir 并 raise PublishError(msg, screenshot_path)
class PublishError(Exception): ...
```

- [ ] **Step 1: 实现 `bjh/publisher/baijiahao.py`**

```python
# bjh/publisher/baijiahao.py
import asyncio
import random
from pathlib import Path

from playwright.async_api import Page, async_playwright

# ==== 选择器常量（百家号改版时只改这里；Task 16 冒烟核对）====
PUBLISH_URL = "https://baijiahao.baidu.com/builder/rc/edit/video"
SEL = {
    "upload_input": "input[type=file]",
    "title_input": "textarea",                    # VERIFY
    "desc_input": "[placeholder*='介绍'],[placeholder*='描述']",  # VERIFY 可选
    "upload_done": "text=转码完成",               # VERIFY 上传成功标志
    "submit_btn": "button:has-text('发布')",      # VERIFY
}


class PublishError(Exception):
    def __init__(self, msg: str, screenshot: str | None = None):
        super().__init__(msg)
        self.screenshot = screenshot


async def _shot(page: Page, shot_dir: Path, name: str) -> str:
    shot_dir.mkdir(parents=True, exist_ok=True)
    p = shot_dir / f"{name}.png"
    await page.screenshot(path=str(p), full_page=True)
    return str(p)


async def _rand():
    await asyncio.sleep(random.uniform(2, 6))


async def publish_video(video_path: Path, title: str, tags: list[str],
                        state_path: Path, shot_dir: Path,
                        dry_run: bool = False) -> str:
    if not state_path.exists():
        raise PublishError(f"登录态不存在: {state_path}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not dry_run)
        ctx = await browser.new_context(storage_state=str(state_path))
        page = await ctx.new_page()
        try:
            await page.goto(PUBLISH_URL, timeout=30000)
            await page.wait_for_load_state("networkidle")
            await _rand()
            # 1. 上传
            upload = page.locator(SEL["upload_input"]).first
            await upload.set_input_files(str(video_path))
            # 2. 等待上传/转码完成（最多 180s，期间轮询标题框可用即视为可填）
            try:
                await page.wait_for_selector(SEL["upload_done"], timeout=180000)
            except Exception:
                pass  # 转码提示文案不一，超时不阻断，靠标题框校验
            await _rand()
            # 3. 填标题与话题
            await page.fill(SEL["title_input"], title)
            if tags:
                try:
                    await page.fill(SEL["desc_input"], " ".join(f"#{t}" for t in tags))
                except Exception:
                    pass  # 描述框可选
            await _rand()
            # 4. 提交
            if dry_run:
                shot = await _shot(page, shot_dir, f"dryrun_{video_path.stem}")
                await browser.close()
                return "DRY-RUN-OK"
            await page.click(SEL["submit_btn"])
            await page.wait_for_timeout(5000)
            shot = await _shot(page, shot_dir, f"submit_{video_path.stem}")
            url = page.url
            await browser.close()
            return url
        except Exception as e:
            shot = None
            try:
                shot = await _shot(page, shot_dir, f"fail_{video_path.stem}")
            except Exception:
                pass
            await browser.close()
            raise PublishError(f"发布失败: {e}", shot) from e
```

- [ ] **Step 2: 语法与导入自检**

Run: `python -c "import bjh.publisher.baijiahao"`
Expected: 无输出（成功）

- [ ] **Step 3: Commit**

```bash
git add bjh/publisher/
git commit -m "feat: 百家号Playwright自动发布(dry-run+失败截图)"
```

---

### Task 12: 风控纯函数（敏感词 / 随机间隔 / 黄金时段）

**Files:**
- Create: `bjh/risk.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: `Config`
- Produces（Task 13/14 消费）：

```python
contains_sensitive(text: str, words: list[str]) -> list[str]     # 命中的词列表
random_interval_sec(cfg: Config, rng: random.Random) -> int
next_publish_time(cfg: Config, now: datetime, rng: random.Random) -> datetime
# 语义：now + 随机间隔；若落点在黄金时段内直接用；否则若 3 小时内有黄金窗口开始，则移到该窗口开始后 1~10 分钟；否则用原落点
```

- [ ] **Step 1: 写失败测试 `tests/test_risk.py`**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_risk.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `bjh/risk.py`**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_risk.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/risk.py tests/test_risk.py
git commit -m "feat: 风控纯函数(敏感词/随机间隔/黄金时段)"
```

---

### Task 13: 文案服务 + 视频构建编排（pipeline）

**Files:**
- Create: `bjh/writer/service.py`、`bjh/pipeline.py`
- Modify: `bjh/db.py`（加 `get_topic`）、`bjh/writer/prompts.py`（加 `load_rules`）
- Test: `tests/test_writer_service.py`、`tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 2/3/4/5/6/7/8/9/12 全部产出
- Produces（CLI 与调度器消费）：

```python
# writer/service.py
def write_draft(db, cfg, topic: Topic, client: DeepSeekClient | None = None) -> Draft
  # 成功 status=ok；命中敏感词重写1次仍中 → status=blocked；两次解析失败 → status=failed；都会入库

# pipeline.py
async def build_video(db, cfg, draft: Draft, topic: Topic) -> Video | None   # 失败入库 status=failed 并返回 None
def create_videos(db, cfg, count: int = 1, topic_id: int | None = None) -> list[int]  # 返回 video ids
async def run_pipeline(db, cfg, keyword: str, count: int = 1, pages: int = 1) -> list[int]
# DB 新增：get_topic(topic_id: int) -> Topic | None
# prompts 新增：load_rules(path: str | None = None) -> str
```

- [ ] **Step 1: Modify `bjh/db.py` 加 `get_topic`（含测试补丁）**

在 `tests/test_db.py::test_topic_select_and_draft_video_post_flow` 中 `db.set_topic_status` 行前加断言：

```python
    assert db.get_topic(best[0].id).title == best[0].title
```

在 `bjh/db.py` 的 `set_topic_status` 上方加：

```python
    def get_topic(self, topic_id: int) -> Topic | None:
        return self._get("topics", "id=?", topic_id)
```

Run: `python -m pytest tests/test_db.py -v` → 2 PASS

- [ ] **Step 2: Modify `bjh/writer/prompts.py` 加 `load_rules`**

```python
def load_rules(path: str | None = None) -> str:
    p = Path(path or SKILL_PATH_DEFAULT)
    if not p.exists():
        p = Path(__file__).resolve().parents[2] / SKILL_PATH_DEFAULT
    return extract_rules(p.read_text(encoding="utf-8"))
```

（文件顶部已有 `from pathlib import Path` 则不重复 import；没有则补。）

- [ ] **Step 3: 写失败测试 `tests/test_writer_service.py`**

```python
import json
from datetime import datetime
import pytest
from bjh.config import Config
from bjh.db import DB
from bjh.models import Topic
from bjh.writer.service import write_draft

T = Topic(keyword="职场", title="月薪三万不敢生孩子", url="u", source_author="a",
          reads=100000, comments=2300, heat_score=1.0, fetched_at="2026-09-29T10:00:00")

# 口播稿必须落在 150~320 字（parse_llm_json 校验）：177 字
GOOD = {"titles": ["月薪三万，为什么不敢生孩子？", "候选二标题", "候选三标题"],
        "title": "月薪三万，为什么不敢生孩子？", "hook": "你敢信吗",
        "script": ("月薪三万。" + "听起来很多的都市白领们。每一个月都在精打细算着过日子。" * 6
                   + "但真的够吗？" + "你怎么看？"),
        "closing_question": "你觉得够不够？", "tags": ["职场", "收入"]}
# 含敏感词"震惊"且字数合法的脏样本（187 字）
BAD = dict(GOOD, title="震惊！月薪三万", script=GOOD["script"] + "这个消息很震惊。")


class FakeClient:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = 0

    def chat(self, messages, json_mode=False, retries=2):
        self.calls += 1
        return self.contents[min(self.calls - 1, len(self.contents) - 1)], \
            {"prompt_tokens": 1000, "completion_tokens": 500}


def test_write_draft_ok(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(GOOD, ensure_ascii=False)))
    assert d.status == "ok" and d.title == GOOD["title"] and d.tags == ["职场", "收入"]
    assert d.cost > 0 and db.get_draft(d.id).hook == "你敢信吗"


def test_write_draft_sensitive_retry_then_blocked(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(BAD, ensure_ascii=False),
                                                       json.dumps(BAD, ensure_ascii=False)))
    assert d.status == "blocked"            # 重写一次仍含"震惊" → blocked


def test_write_draft_retry_recovers(tmp_path):
    db = DB(tmp_path / "t.db")
    d = write_draft(db, Config(), T, client=FakeClient(json.dumps(BAD, ensure_ascii=False),
                                                       json.dumps(GOOD, ensure_ascii=False)))
    assert d.status == "ok"                 # 第二次干净 → 通过


def test_write_draft_needs_key_without_client(tmp_path):
    db = DB(tmp_path / "t.db")
    with pytest.raises(ValueError, match="deepseek_api_key"):
        write_draft(db, Config(), T)
```

（GOOD.script = 4 + 27×6 + 6 + 5 = 177 字；BAD.script = 177 + 8 = 185 字，均在 150~320 合法区间，且 BAD 含敏感词"震惊"——字数先过校验、再被敏感词拦截，两条路径都被覆盖。）

- [ ] **Step 4: 实现 `bjh/writer/service.py`**

```python
# bjh/writer/service.py
from datetime import datetime

from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic
from bjh.risk import contains_sensitive
from bjh.writer.deepseek import DeepSeekClient, estimate_cost
from bjh.writer.prompts import build_system_prompt, build_user_prompt, load_rules, parse_llm_json, pick_title


def write_draft(db: DB, cfg: Config, topic: Topic,
                client: DeepSeekClient | None = None) -> Draft:
    if client is None:
        if not cfg.deepseek_api_key:
            raise ValueError("缺少 deepseek_api_key（config.toml 或 BJH_DEEPSEEK_API_KEY）")
        client = DeepSeekClient(cfg.deepseek_api_key, cfg.deepseek_base_url, cfg.deepseek_model)
    system = build_system_prompt(load_rules())
    user = build_user_prompt(topic)
    cost = 0.0
    for attempt in (1, 2):
        raw, usage = client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True)
        cost += estimate_cost(usage, cfg)
        try:
            data = parse_llm_json(raw)
        except ValueError:
            if attempt == 2:
                return _insert(db, topic, title="", script="", tags=[], hook="",
                               closing_question="", status="failed", cost=cost)
            continue
        title = data["title"].strip() or pick_title(data["titles"])
        hits = contains_sensitive(title + data["script"], cfg.sensitive_words)
        if not hits:
            return _insert(db, topic, title=title, script=data["script"], tags=data["tags"],
                           hook=data["hook"], closing_question=data["closing_question"],
                           status="ok", cost=cost)
        user = user + f"\n上一版命中平台违禁词{hits}，请彻底换角度重写。"
    return _insert(db, topic, title="", script="", tags=[], hook="",
                   closing_question="", status="blocked", cost=cost)


def _insert(db, topic, *, title, script, tags, hook, closing_question, status, cost) -> Draft:
    d = Draft(topic_id=topic.id, title=title, script=script, tags=tags, hook=hook,
              closing_question=closing_question, model="deepseek-chat",
              cost=round(cost, 6), status=status,
              created_at=datetime.now().isoformat(timespec="seconds"))
    db.insert_draft(d)
    return d
```

- [ ] **Step 5: 运行 writer 测试确认通过**

Run: `python -m pytest tests/test_writer_service.py -v`
Expected: 4 PASS

- [ ] **Step 6: 写失败测试 `tests/test_pipeline.py`**

```python
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
```

- [ ] **Step 7: 实现 `bjh/pipeline.py`**

```python
# bjh/pipeline.py
import random
from datetime import datetime
from pathlib import Path

from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Topic, Video
from bjh.risk import next_publish_time
from bjh.studio.compose import audio_duration, compose_video
from bjh.studio.images import collect_images
from bjh.studio.subtitle import build_ass
from bjh.studio.tts import split_sentences, sentences_with_timing, synthesize_with_retry
from bjh.writer.service import write_draft
from bjh.writer.topic import dedupe_topics, select_topics


async def build_video(db: DB, cfg: Config, draft: Draft, topic: Topic) -> Video | None:
    work = Path(cfg.data_dir) / "videos" / f"draft_{draft.id}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        mp3 = work / "voice.mp3"
        words = await synthesize_with_retry(draft.script, cfg.tts_voice, mp3)
        sents = sentences_with_timing(words, split_sentences(draft.script))
        ass = work / "subs.ass"
        ass.write_text(build_ass(sents), encoding="utf-8")
        safe_ass = work / "subs.ass"        # 简单文件名，避免 subtitles 滤镜路径转义问题
        images = await collect_images(topic.keyword, 6, work / "img",
                                      cfg.pexels_api_key or None,
                                      [draft.title, draft.hook or draft.title])
        out = work / f"{draft.id}.mp4"
        compose_video(mp3, images, safe_ass, out)
        v = Video(draft_id=draft.id, path=str(out), duration_sec=audio_duration(out),
                  size_bytes=out.stat().st_size, cost=draft.cost, status="ok",
                  error=None, created_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:
        v = Video(draft_id=draft.id, path="", duration_sec=0.0, size_bytes=0,
                  cost=0.0, status="failed", error=str(e),
                  created_at=datetime.now().isoformat(timespec="seconds"))
    db.insert_video(v)
    return v if v.status == "ok" else None


def create_videos(db: DB, cfg: Config, count: int = 1,
                  topic_id: int | None = None) -> list[int]:
    import asyncio
    if topic_id is not None:
        topics = [db.get_topic(topic_id)]
    else:
        used = [d.title for d in db.list_drafts(limit=500)]
        topics = select_topics(db.unused_topics(n=count * 5), n=count, used_titles=used)
    video_ids: list[int] = []
    for t in topics:
        if not t:
            continue
        db.set_topic_status(t.id, "selected")
        draft = write_draft(db, cfg, t)
        if draft.status != "ok":
            db.set_topic_status(t.id, "skipped")
            continue
        video = asyncio.run(build_video(db, cfg, draft, t))
        if video:
            video_ids.append(video.id)
        else:
            db.set_topic_status(t.id, "skipped")
    return video_ids


def fetch_and_store(db: DB, keyword: str, pages: int, cfg: Config) -> int:
    import asyncio
    from bjh.fetcher.baijiahao import fetch_topics
    acc = db.get_account("default") or (db.list_accounts() or [None])[0]
    if not acc:
        raise SystemExit("无账号，请先 bjh login")
    raw = asyncio.run(fetch_topics(keyword, pages, acc))
    seen = {t.title for t in db.list_topics(keyword, limit=500)}
    fresh = [t for t in dedupe_topics(raw) if t.title not in seen]
    for t in fresh:
        db.insert_topic(t)
    return len(fresh)


def run_pipeline(db: DB, cfg: Config, keyword: str, count: int = 1, pages: int = 1) -> list[int]:
    acc = db.get_account("default") or (db.list_accounts() or [None])[0]
    if not acc:
        raise SystemExit("无账号，请先 bjh login")
    fetch_and_store(db, keyword, pages, cfg)
    video_ids = create_videos(db, cfg, count=count)
    rng = random.Random()
    t = next_publish_time(cfg, datetime.now(), rng)
    for vid in video_ids:
        db.queue_post(vid, acc.id, t.isoformat(timespec="seconds"))
        t = next_publish_time(cfg, t, rng)
    return video_ids
```

- [ ] **Step 8: 运行确认通过**

Run: `python -m pytest tests/test_pipeline.py tests/test_db.py -v`
Expected: 4 PASS

- [ ] **Step 9: Commit**

```bash
git add bjh/writer/service.py bjh/pipeline.py bjh/db.py bjh/writer/prompts.py tests/
git commit -m "feat: 文案服务与视频构建编排流水线"
```

---

### Task 14: 发布调度器（daemon + 日上限）

**Files:**
- Create: `bjh/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `DB`、`Config`、`bjh.publisher.baijiahao.publish_video / PublishError`、`bjh.risk.next_publish_time`
- Produces（CLI 消费）：

```python
async def run_once(db: DB, cfg: Config, dry_run: bool = False,
                   publish_fn=None) -> str
# 返回："idle" | "published" | "failed" | "account-expired" | "daily-limit" | "dry-run"
# dry_run=True 时调用 publish_fn(dry_run=True) 且不改 posts 状态
# 账号 expired → post 顺延 next_publish_time 并返回 "account-expired"
# 当日已达 daily_limit → post 顺延到次日首个黄金窗口 返回 "daily-limit"

async def run_daemon(db, cfg, dry_run: bool = False) -> None   # 循环 run_once，每 60s 轮询
```

- [ ] **Step 1: 写失败测试 `tests/test_scheduler.py`**

```python
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from bjh.config import Config
from bjh.db import DB
from bjh.models import Draft, Video
from bjh.publisher.baijiahao import PublishError
from bjh.scheduler import run_once


def seed(tmp_path, daily_limit=2):
    db = DB(tmp_path / "t.db")
    acc = db.upsert_account("default", daily_limit=daily_limit)
    d = Draft(topic_id=1, title="标题", script="s", tags=[], hook="", closing_question="",
              model="m", cost=0.01, status="ok", created_at="2026-09-29T10:00:00")
    db.insert_draft(d)
    v = Video(draft_id=d.id, path="data/videos/x.mp4", duration_sec=60.0, size_bytes=1,
              cost=0.01, status="ok", error=None, created_at="2026-09-29T10:00:00")
    db.insert_video(v)
    return db, acc, v


def past():
    return (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")


async def fake_ok(video_path, title, tags, state_path, shot_dir, dry_run=False):
    return "https://baijiahao.baidu.com/s?id=ok"


async def fake_fail(video_path, title, tags, state_path, shot_dir, dry_run=False):
    raise PublishError("boom", "shot.png")


async def test_run_once_publishes(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "published"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "published" and post.bjh_url.endswith("id=ok")
    assert db.published_today(acc.id) == 1


async def test_run_once_failure_recorded(tmp_path):
    db, acc, v = seed(tmp_path)
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_fail)
    assert result == "failed"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "failed" and "boom" in post.error and post.screenshot_path


async def test_run_once_daily_limit_reschedules(tmp_path):
    db, acc, v = seed(tmp_path, daily_limit=1)
    db.queue_post(v.id, acc.id, past())
    await run_once(db, Config(), publish_fn=fake_ok)          # 今日 1/1
    v2 = Video(draft_id=1, path="p2", duration_sec=60.0, size_bytes=1, cost=0,
               status="ok", error=None, created_at="2026-09-29T11:00:00")
    db.insert_video(v2)
    pid = db.queue_post(v2.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "daily-limit"
    post = [p for p in db.list_posts() if p.id == pid][0]
    assert post.status == "queued"
    assert post.scheduled_at > datetime.now().isoformat(timespec="seconds")[:10]  # 排到未来日期


async def test_run_once_expired_account(tmp_path):
    db, acc, v = seed(tmp_path)
    db.set_account_status(acc.id, "expired")
    pid = db.queue_post(v.id, acc.id, past())
    result = await run_once(db, Config(), publish_fn=fake_ok)
    assert result == "account-expired"
    assert [p for p in db.list_posts() if p.id == pid][0].status == "queued"


async def test_run_once_idle(tmp_path):
    db, acc, v = seed(tmp_path)
    assert await run_once(db, Config(), publish_fn=fake_ok) == "idle"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `bjh/scheduler.py`**

```python
# bjh/scheduler.py
import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from bjh.config import Config
from bjh.db import DB
from bjh.models import Post
from bjh.publisher.baijiahao import PublishError, publish_video
from bjh.risk import next_publish_time

log = logging.getLogger("bjh.scheduler")


async def run_once(db: DB, cfg: Config, dry_run: bool = False, publish_fn=None) -> str:
    publish_fn = publish_fn or publish_video
    now = datetime.now()
    posts = db.due_posts(now.isoformat(timespec="seconds"))
    if not posts:
        return "idle"
    post = posts[0]
    account = db.get_account_by_id(post.account_id)
    rng = random.Random()

    if account is None:
        post.status, post.error = "failed", "账号不存在"
        db.update_post(post)
        return "failed"
    if account.status != "active":
        post.scheduled_at = next_publish_time(cfg, now, rng).isoformat(timespec="seconds")
        db.update_post(post)
        return "account-expired"
    if db.published_today(account.id) >= account.daily_limit:
        tomorrow650 = datetime.combine(now.date() + timedelta(days=1), time(6, 50))
        post.scheduled_at = next_publish_time(cfg, tomorrow650, rng).isoformat(timespec="seconds")
        db.update_post(post)
        return "daily-limit"

    video = db.get_video(post.video_id)
    draft = db.get_draft(video.draft_id)
    shot_dir = Path(cfg.data_dir) / "screenshots"
    if dry_run:
        await publish_fn(Path(video.path), draft.title, draft.tags,
                         Path(account.storage_state_path), shot_dir, dry_run=True)
        return "dry-run"

    post.status = "publishing"
    db.update_post(post)
    try:
        url = await publish_fn(Path(video.path), draft.title, draft.tags,
                               Path(account.storage_state_path), shot_dir)
        post.status, post.published_at, post.bjh_url = "published", \
            datetime.now().isoformat(timespec="seconds"), url
        db.update_post(post)
        return "published"
    except PublishError as e:
        post.status, post.error, post.screenshot_path = "failed", str(e), e.screenshot
        db.update_post(post)
        log.error("发布失败 post=%s: %s", post.id, e)
        return "failed"
    except Exception as e:                       # 非预期异常也落库，不阻塞队列
        post.status, post.error = "failed", f"未预期错误: {e}"
        db.update_post(post)
        return "failed"


async def run_daemon(db: DB, cfg: Config, dry_run: bool = False) -> None:
    log.info("调度器启动")
    while True:
        try:
            result = await run_once(db, cfg, dry_run)
            if result != "idle":
                log.info("run_once -> %s", result)
        except Exception:
            log.exception("调度循环异常")
        await asyncio.sleep(60)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add bjh/scheduler.py tests/test_scheduler.py
git commit -m "feat: 发布调度器(随机间隔/日上限/dry-run)"
```

---

### Task 15: CLI（typer 子命令组装）

**Files:**
- Create: `bjh/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 全部上游模块
- Produces: `bjh` 入口（pyproject 已注册 `bjh = "bjh.cli:app"`），命令：
  `login / fetch / create / publish / run / web`

- [ ] **Step 1: 写失败测试 `tests/test_cli.py`**

```python
from typer.testing import CliRunner
from bjh.cli import app

runner = CliRunner()


def test_help_lists_all_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("login", "fetch", "create", "publish", "run", "web"):
        assert cmd in r.output


def test_fetch_requires_keyword():
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code != 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL（ModuleNotFoundError: bjh.cli）

- [ ] **Step 3: 实现 `bjh/cli.py`**

```python
# bjh/cli.py
import asyncio
from datetime import datetime
from pathlib import Path

import typer

from bjh.config import Config, load_config
from bjh.db import DB

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="百家号视频自动化流水线")


def _cfg() -> Config:
    return load_config()


def _db(cfg: Config) -> DB:
    d = Path(cfg.data_dir)
    d.mkdir(parents=True, exist_ok=True)
    return DB(d / "bjh.db")


def _default_account(db: DB):
    return db.get_account("default") or (db.list_accounts() or [None])[0]


@app.command()
def login(account: str = typer.Option("default", help="账号名，默认 default")):
    """打开浏览器扫码登录百家号，保存 Cookie。"""
    from bjh.fetcher.baijiahao import login as do_login
    acc = asyncio.run(do_login(account, _cfg()))
    typer.echo(f"✔ 账号 [{acc.name}] 登录态已保存")


@app.command()
def fetch(keyword: str = typer.Argument(..., help="搜索关键词"),
          pages: int = typer.Option(1, help="抓取页数")):
    """抓取关键词下的百家号热文（含阅读/评论）入库。"""
    from bjh.pipeline import fetch_and_store
    cfg = _cfg()
    n = fetch_and_store(_db(cfg), keyword, pages, cfg)
    typer.echo(f"✔ 新增热文 {n} 条")


@app.command()
def create(count: int = typer.Option(1, help="生成视频数"),
           topic_id: int = typer.Option(None, help="指定选题 id（可选）")):
    """选题 → DeepSeek 文案 → 合成视频。"""
    from bjh.pipeline import create_videos
    cfg = _cfg()
    ids = create_videos(_db(cfg), cfg, count=count, topic_id=topic_id)
    typer.echo(f"✔ 生成视频 {len(ids)} 个: {ids}" if ids else "✘ 无可用视频（选题耗尽或文案被拦截）")


@app.command()
def publish(now: bool = typer.Option(False, "--now", help="立即发布最新未发视频"),
            daemon: bool = typer.Option(False, "--daemon", help="常驻调度"),
            dry_run: bool = typer.Option(False, "--dry-run", help="演练到提交前一步")):
    """发布队列：--now 单发 / --daemon 常驻 / --dry-run 演练。"""
    from bjh.scheduler import run_daemon, run_once
    cfg = _cfg()
    db = _db(cfg)
    acc = _default_account(db)
    if not acc:
        raise typer.Exit("无账号，请先 bjh login")
    if now:
        vids = [v["id"] for v in db.videos_with_titles(limit=500)
                if v["status"] == "ok" and v["id"] not in
                {p.video_id for p in db.list_posts(limit=500)}]
        if not vids:
            raise typer.Exit("没有待发视频，先 bjh create")
        db.queue_post(vids[0], acc.id, datetime.now().isoformat(timespec="seconds"))
    if daemon:
        typer.echo("调度器运行中（Ctrl-C 退出）...")
        asyncio.run(run_daemon(db, cfg, dry_run))
    else:
        result = asyncio.run(run_once(db, cfg, dry_run))
        typer.echo(f"✔ run_once -> {result}")


@app.command()
def run(keyword: str = typer.Argument(..., help="搜索关键词"),
        count: int = typer.Option(1, help="产量"),
        pages: int = typer.Option(1, help="抓取页数")):
    """一键全流程：抓热文 → 生成视频 → 入发布队列。"""
    from bjh.pipeline import run_pipeline
    cfg = _cfg()
    ids = run_pipeline(_db(cfg), cfg, keyword, count=count, pages=pages)
    typer.echo(f"✔ 流水线完成，视频 {ids} 已入队；用 bjh publish --daemon 自动发布")


@app.command()
def web(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8787)):
    """启动 Web 管理面板。"""
    import uvicorn
    from bjh.server.api import create_app
    cfg = _cfg()
    uvicorn.run(create_app(_db(cfg), cfg), host=host, port=port)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: 运行确认通过 + 安装后验证入口**

Run: `python -m pytest tests/test_cli.py -v && bjh --help`
Expected: 2 PASS；`bjh --help` 列出 6 个命令

- [ ] **Step 5: Commit**

```bash
git add bjh/cli.py tests/test_cli.py
git commit -m "feat: typer CLI(login/fetch/create/publish/run/web)"
```

---

### Task 16: Web 面板（FastAPI + Alpine.js）

**Files:**
- Create: `bjh/server/__init__.py`（空文件）、`bjh/server/api.py`、`bjh/server/static/index.html`
- Modify: `bjh/config.py`（加 `save_config`）
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `DB`、`Config`
- Produces: `create_app(db: DB, cfg: Config) -> FastAPI`（CLI `web` 命令消费）；`save_config(cfg, path=None) -> None`（只写简单字段，golden_hours/敏感词表保持默认）

API：
```
GET  /api/topics?keyword=&limit=        → list[Topic dict]
GET  /api/drafts                        → list[Draft dict]
PATCH /api/drafts/{id}  {title}         → {ok:true}
GET  /api/videos                        → list[dict]（含 draft_title）
GET  /api/videos/{id}/file              → FileResponse mp4
GET  /api/posts?status=                 → list[Post dict]
POST /api/posts  {video_id}             → {post_id}（scheduled_at=now，立即待发）
GET  /api/accounts                      → list[Account dict]
PATCH /api/accounts/{id} {daily_limit}  → {ok:true}
GET  /api/config                        → Config 关键字段 dict
PATCH /api/config {daily_limit?, interval_min_min?, interval_max_min?}  → 更新内存+写 config.toml
GET  /api/stats                         → {today_published, total_videos, total_cost}
GET  /                                   → index.html
```

- [ ] **Step 1: Modify `bjh/config.py` 追加 `save_config`**

```python
_SAVE_FIELDS = ("deepseek_api_key", "deepseek_base_url", "deepseek_model", "pexels_api_key",
                "tts_voice", "daily_limit", "interval_min_min", "interval_max_min")


def save_config(cfg: Config, path: str | None = None) -> None:
    p = Path(path or DEFAULT_CONFIG_NAME)
    lines = [f'{f} = "{getattr(cfg, f)}"' if isinstance(getattr(cfg, f), str)
             else f"{f} = {getattr(cfg, f)}" for f in _SAVE_FIELDS]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 2: 写失败测试 `tests/test_api.py`**

```python
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
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 4: 实现 `bjh/server/api.py`**

```python
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
```

- [ ] **Step 5: 实现 `bjh/server/static/index.html`**

```html
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8"><title>百家号流水线</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="panel()" x-init="init()">
  <nav class="flex gap-1 p-4 border-b border-gray-800">
    <template x-for="t in ['stats','topics','videos','posts','settings']">
      <button @click="tab=t" x-text="({stats:'统计',topics:'热榜',videos:'视频',posts:'队列',settings:'设置'})[t]"
              :class="tab===t ? 'bg-blue-600' : 'bg-gray-800'"
              class="px-4 py-2 rounded"></button>
    </template>
    <span class="ml-auto text-sm text-gray-400 self-center" x-text="hint"></span>
  </nav>

  <main class="p-4 max-w-5xl mx-auto">
    <section x-show="tab==='stats'" class="grid grid-cols-3 gap-4">
      <div class="bg-gray-900 rounded p-6 text-center"><div class="text-4xl" x-text="s.today_published"></div>今日已发</div>
      <div class="bg-gray-900 rounded p-6 text-center"><div class="text-4xl" x-text="s.total_videos"></div>累计视频</div>
      <div class="bg-gray-900 rounded p-6 text-center"><div class="text-4xl" x-text="(s.total_cost||0).toFixed(3)"></div>累计成本(元)</div>
    </section>

    <section x-show="tab==='topics'">
      <input x-model="kw" @keyup.enter="refresh()" placeholder="关键词过滤" class="bg-gray-900 rounded px-3 py-2 mb-3">
      <table class="w-full text-sm"><tbody>
        <template x-for="t in topics"><tr class="border-b border-gray-800">
          <td class="py-1" x-text="t.title"></td>
          <td x-text="'R'+t.reads"></td><td x-text="'C'+t.comments"></td>
          <td x-text="t.heat_score.toFixed(1)"></td><td x-text="t.status"></td>
        </tr></template>
      </tbody></table>
    </section>

    <section x-show="tab==='videos'">
      <template x-for="v in videos"><div class="bg-gray-900 rounded p-3 mb-2 flex items-center gap-3">
        <video class="w-24 rounded" :src="'/api/videos/'+v.id+'/file'" controls muted></video>
        <div class="flex-1">
          <div x-text="v.draft_title"></div>
          <div class="text-xs text-gray-400" x-text="v.duration_sec.toFixed(0)+'s · ¥'+v.cost.toFixed(3)+' · '+v.status"></div>
        </div>
        <button @click="retitle(v.draft_id)" class="bg-gray-700 px-2 py-1 rounded text-sm">改标题</button>
        <button @click="queue(v.id)" class="bg-blue-600 px-2 py-1 rounded text-sm">入队</button>
      </div></template>
    </section>

    <section x-show="tab==='posts'">
      <table class="w-full text-sm"><tbody>
        <template x-for="p in posts"><tr class="border-b border-gray-800">
          <td x-text="'#'+p.id"></td><td x-text="p.status"></td>
          <td x-text="p.scheduled_at"></td><td x-text="p.bjh_url||p.error||''"></td>
          <td><a x-show="p.screenshot_path" :href="p.screenshot_path" class="underline">截图</a></td>
        </tr></template>
      </tbody></table>
    </section>

    <section x-show="tab==='settings'">
      <div class="flex items-center gap-2 mb-3">
        日上限 <input type="number" x-model.number="c.daily_limit" @change="saveCfg()" class="bg-gray-900 rounded px-2 py-1 w-20">
        间隔下限(分) <input type="number" x-model.number="c.interval_min_min" @change="saveCfg()" class="bg-gray-900 rounded px-2 py-1 w-20">
        间隔上限(分) <input type="number" x-model.number="c.interval_max_min" @change="saveCfg()" class="bg-gray-900 rounded px-2 py-1 w-20">
      </div>
      <template x-for="a in accounts"><div class="bg-gray-900 rounded p-3 mb-2 flex justify-between">
        <span x-text="a.name + (a.status==='expired' ? ' ⚠Cookie失效，请 bjh login' : '')"></span>
        <span x-text="'日上限 '+a.daily_limit"></span>
      </div></template>
    </section>
  </main>

<script>
function panel(){return{tab:'stats',kw:'',hint:'',topics:[],videos:[],posts:[],accounts:[],s:{},c:{},
  async init(){await this.refresh();setInterval(()=>this.refresh(),5000)},
  async refresh(){
    const g=async u=>(await fetch(u)).json();
    this.s=await g('/api/stats');
    this.topics=await g('/api/topics'+(this.kw?('?keyword='+encodeURIComponent(this.kw)):''));
    this.videos=await g('/api/videos');
    this.posts=await g('/api/posts');
    this.accounts=await g('/api/accounts');
    this.c=await g('/api/config');
    this.hint='今日 '+this.s.today_published+' · 自动刷新中';
  },
  async retitle(id){const t=prompt('新标题');if(!t)return;
    await fetch('/api/drafts/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:t})});this.refresh()},
  async queue(id){await fetch('/api/posts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_id:id})});this.refresh()},
  async saveCfg(){await fetch('/api/config',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(this.c)})}
}}
</script>
</body></html>
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_api.py -v`
Expected: 5 PASS

- [ ] **Step 7: （手动冒烟）面板启动**

Run: `bjh web` → 打开 http://127.0.0.1:8787
Expected: 统计卡显示数据、热榜可过滤、视频可预览、设置可保存

- [ ] **Step 8: Commit**

```bash
git add bjh/server/ bjh/config.py tests/test_api.py
git commit -m "feat: Web管理面板(FastAPI+Alpine)"
```

---

### Task 17: README + 全量回归 + 真机端到端冒烟

**Files:**
- Create: `README.md`
- Test: 全部 `tests/`

**Interfaces:**
- Consumes: 全部
- Produces: 可交付的完整项目文档与冒烟记录

- [ ] **Step 1: 写 `README.md`**

````markdown
# bjh — 百家号视频自动化流水线

关键词 → 抓百家号热文（阅读/评论） → DeepSeek 原创口播文案 → edge-tts 配音 + FFmpeg 合成竖屏视频 → 随机间隔自动发布。单条现金成本 ≈0.02~0.05 元（DeepSeek 仅有）。

## 安装
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
brew install ffmpeg          # macOS；Linux: apt install ffmpeg
cp config.example.toml config.toml   # 填 deepseek_api_key（https://platform.deepseek.com）
```

## 使用
```bash
bjh login                                    # 扫码登录（Cookie 存 data/accounts/default.json）
bjh fetch --keyword "职场焦虑"               # 抓热文入库
bjh create --count 1                         # 生成文案+视频
bjh publish --dry-run                        # 发布演练（截图确认，不真发）
bjh publish --now                            # 立即真发 1 条
bjh publish --daemon                         # 常驻调度（日上限+随机间隔+黄金时段）
bjh run --keyword "职场焦虑" --count 3       # 一键全流程（抓→做→入队）
bjh web                                      # 管理面板 http://127.0.0.1:8787
```

## Claude Code 半自动模式
skills/bjh-video/SKILL.md 封装了选题/标题/文案/视频/发布节奏规则；在 Claude Code 里可逐步驱动上述 CLI 并人工审查每步产出。

## 风控注意
- 单账号日上限默认 8（面板可调）；平台对高频机器发文有风控，间隔默认 25~50 分钟随机
- Cookie 失效在面板"设置"页红字提示，重新 `bjh login` 即可
- 内容必须原创；敏感词命中会自动重写/拦截（drafts.status=blocked）

## 成本
| 项 | 成本 |
|---|---|
| edge-tts 配音 | 0 |
| Pexels 配图（无 Key 用模板图） | 0 |
| FFmpeg 合成 | 0（本地） |
| DeepSeek 文案 | ≈0.02 元/条 |
````

- [ ] **Step 2: 全量回归**

Run: `python -m pytest -v`
Expected: 全部 PASS（无 ffmpeg 环境的 compose 测试 SKIP）

- [ ] **Step 3: （真机端到端冒烟，人工执行，记录结果到提交说明）**

```bash
bjh login
bjh fetch --keyword "职场焦虑" --pages 1     # 确认抓到热文（若 0 条 → 校正 fetcher 常量后重跑）
bjh create --count 1                         # 确认 data/videos/draft_N/N.mp4 可播放、字幕正确
bjh web                                      # 面板核对
bjh publish --dry-run                        # data/screenshots/dryrun_*.png 核对表单已填好
bjh publish --now                            # 真发 1 条，面板确认 posts.published + bjh_url
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README与端到端冒烟清单"
```
