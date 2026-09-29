# bjh/fetcher/baijiahao.py
import re
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from bjh.config import Config
from bjh.models import Account, Topic
from bjh.publisher.baijiahao import is_login_url, LoginExpiredError, USER_AGENT
from bjh.writer.topic import heat_score

TopicRaw = namedtuple("TopicRaw", ["title", "url", "author"])

# ==== 选择器/URL 常量（真实站点变动时只需改这里；Task 10 Step 6 冒烟核对）====
BAIDU_SERP_URL = "https://www.baidu.com/s?wd={kw}&rn=20"
BJH_LINK_MARK = "baijiahao.baidu.com/s?id="            # VERIFY: 真实 SERP 链接格式
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

    def _add(text: str, url: str):
        title = TITLE_SUFFIX.sub("", (text or "").strip()).strip()
        if len(title) < 8 or title in seen:
            return
        seen.add(title)
        out.append(TopicRaw(title=title, url=url, author=""))

    # 新版 SERP（2026-09 实测）：结果真实 URL 在容器 mu 属性上，href 均为 baidu.com/link 跳转
    for el in soup.select("div.result[mu]"):
        mu = el.get("mu", "")
        if BJH_LINK_MARK not in mu:
            continue
        h = el.find("h3") or el.find("a")
        if h is not None:
            _add(h.get_text(), mu)
    if out:
        return out
    # 兜底：旧版 SERP 锚点 href 直接是百家号链接
    for a in soup.find_all("a", href=True):
        if BJH_LINK_MARK in a["href"]:
            _add(a.get_text(), a["href"])
    return out


def parse_article_stats(html: str) -> dict:
    return {"reads": _first_int(html, READ_PATTERNS), "comments": _first_int(html, COMMENT_PATTERNS)}


async def login(account_name: str, cfg: Config) -> Account:
    """打开浏览器等用户扫码，保存 storage_state。"""
    state_path = Path(cfg.data_dir) / "accounts" / f"{account_name}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(user_agent=USER_AGENT)
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
        ctx = await browser.new_context(storage_state=str(state), user_agent=USER_AGENT)
        page = await ctx.new_page()
        for pn in range(pages):
            await page.goto(BAIDU_SERP_URL.format(kw=keyword) + (f"&pn={pn * 20}" if pn else ""))
            await page.wait_for_timeout(1500)
            if is_login_url(page.url):   # Cookie 失效被重定向到登录页
                await browser.close()
                raise LoginExpiredError()
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
