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
        db.queue_post(vids[-1], acc.id, datetime.now().isoformat(timespec="seconds"))  # 最旧优先，与 daemon FIFO 一致
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
