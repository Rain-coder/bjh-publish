# bjh/publisher/baijiahao.py
import asyncio
import random
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

# ==== 选择器常量（百家号改版时只改这里；2026-09-29 真机核对）====
BUILDER_HOME = "https://baijiahao.baidu.com/builder/rc/home"
# 真实桌面 UA：Playwright 默认无头 UA 会被百度 SERP 识别为爬虫返回简化页（2026-09 实测）
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
SEL = {
    "publish_entry": "text=发布作品",               # 左上角 + 按钮（实测为文字按钮）
    "video_tab": "text=视频",                       # 编辑器顶部内容类型 tab
    "upload_input": "input[type=file]",
    "form_ready": "text=作品描述",                   # 上传后表单区出现的标志
    "title_input": ".videov2-title-wrap [contenteditable=true]",   # 作品描述框（DIV富文本，限50字）
    "cover_first": "img[src*='bjhvideocover']",     # 智能推荐封面缩略图（必选其一，否则报"请添加横版封面"）
    "decl_input": "[placeholder='请选择创作声明']",   # 创作声明必填下拉
    "decl_option": "text=含AI生成内容",              # 如实申报 AI 生成（文案/TTS 均为 AI 产出）
    "submit_btn": "button",                         # 精确文本匹配在代码里做（避开"定时发布"）
}
TITLE_MAX = 50                                      # 作品描述字数上限（实测 1/50）
SECURITY_MARK = "安全验证"                          # 百度滑块验证弹窗标志


class PublishError(Exception):
    def __init__(self, msg: str, screenshot: str | None = None):
        super().__init__(msg)
        self.screenshot = screenshot


# Cookie 失效判定：跳转到登录/通行证页即视为过期（spec §5）
LOGIN_URL_MARKS = ("passport.baidu.com", "baijiahao.baidu.com/builder/rc/login")


def is_login_url(url: str) -> bool:
    return any(m in url for m in LOGIN_URL_MARKS)


class LoginExpiredError(PublishError):
    """Cookie 失效被重定向到登录页；调用方应将账号标记为 expired。"""

    def __init__(self, msg: str = "Cookie 已失效（跳转到登录页）", screenshot: str | None = None):
        super().__init__(msg, screenshot)


async def _shot(page: Page, shot_dir: Path, name: str) -> str:
    shot_dir.mkdir(parents=True, exist_ok=True)
    p = shot_dir / f"{name}.png"
    await page.screenshot(path=str(p), full_page=True)
    return str(p)


async def _rand():
    await asyncio.sleep(random.uniform(2, 6))


async def _wait_security_cleared(page: Page, max_wait: float = 120.0):
    """滑块/安全验证弹窗出现时等人工处理（有头模式，本地机器前有人）。"""
    import time
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        html = await page.content()
        if SECURITY_MARK not in html:
            return
        await page.wait_for_timeout(5000)
    raise PublishError(f"安全验证 {max_wait}s 内未通过（需人工滑块）")


async def _watch_submit(page: Page, max_wait: float = 45.0) -> tuple[str, str]:
    """点「发布」后观察结果：ok=(跳转/成功提示)；error=校验 toast 文案。"""
    import time
    start_url = page.url
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if page.url != start_url and "edit" not in page.url:
            return "ok", page.url                       # 跳离编辑页 → 成功
        layers = await page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll(
                    '[class*=modal],[class*=dialog],[class*=toast],[class*=Message],[class*=notice]')) {
                if (!el.offsetWidth && !el.offsetHeight) continue;
                const t = (el.innerText||'').trim();
                if (t && t.length < 200) out.push(t);
            }
            return out;
        }""")
        for t in layers:
            flat = t.replace("\n", " ")
            if ("发布成功" in flat or "审核" in flat and "中" in flat):
                return "ok", flat
            if flat.startswith(("请添加", "请选择", "请填写", "请输入")):
                return "error", flat                    # 表单校验被拒
        await page.wait_for_timeout(2000)
    return "unknown", f"{max_wait}s 内未见成功或校验提示"


async def publish_video(video_path: Path, title: str, tags: list[str],
                        state_path: Path, shot_dir: Path,
                        dry_run: bool = False) -> str:
    if not state_path.exists():
        raise PublishError(f"登录态不存在: {state_path}")
    async with async_playwright() as pw:
        # 始终有头：无头模式实测触发百度安全验证（滑块），导致编辑页空白
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(storage_state=str(state_path), user_agent=USER_AGENT)
        page = await ctx.new_page()
        try:
            # 0. 后台首页 → 处理可能的滑块验证
            await page.goto(BUILDER_HOME, timeout=60000)
            await page.wait_for_timeout(4000)
            if is_login_url(page.url):
                shot = await _shot(page, shot_dir, f"login_expired_{video_path.stem}")
                raise LoginExpiredError(screenshot=shot)
            await _wait_security_cleared(page)
            # 1. 发布作品 → 视频 tab（新后台无直达 URL，旧 edit/video 路由已空白）
            await page.locator(SEL["publish_entry"]).first.click(timeout=15000)
            await _rand()
            await page.locator(SEL["video_tab"]).first.click(timeout=15000)
            await _rand()
            # 2. 上传（隐藏 input 可直接 set_input_files）
            upload = page.locator(SEL["upload_input"]).first
            await upload.wait_for(state="attached", timeout=30000)
            await upload.set_input_files(str(video_path))
            # 3. 等上传+转码：「作品描述」区出现即表单就绪（最长 5 分钟）
            await page.locator(SEL["form_ready"]).first.wait_for(state="visible", timeout=300000)
            title_box = page.locator(SEL["title_input"]).first
            await title_box.wait_for(state="visible", timeout=30000)
            await _rand()
            # 4. 填标题（话题以 # 拼在标题尾，视频页无独立简介框；上限50字）
            full_title = title if not tags else f"{title} {' '.join(f'#{t}' for t in tags)}"
            await title_box.fill(full_title[:TITLE_MAX])
            await _rand()
            # 5. 选智能推荐封面（不选则提交报"请添加横版封面"）
            cover = page.locator(SEL["cover_first"]).first
            try:
                await cover.wait_for(state="visible", timeout=60000)
                await cover.click()
                await _rand()
            except Exception as e:      # 推荐未生成（罕见）→ 留给提交校验兜底报错
                print(f"[publisher] 封面选择跳过: {e}")
            # 6. 创作声明：如实选「含AI生成内容」（文案/TTS 均 AI 产出），确定
            decl = page.locator(SEL["decl_input"]).first
            await decl.click(timeout=10000)
            await page.locator(SEL["decl_option"]).first.click(timeout=10000)
            ok_btn = page.get_by_role("button", name="确定", exact=True)
            if await ok_btn.count() and await ok_btn.first.is_visible():
                await ok_btn.first.click(timeout=5000)
            await _rand()
            # 7. 提交（精确匹配"发布"，避开"定时发布"）
            if dry_run:
                shot = await _shot(page, shot_dir, f"dryrun_{video_path.stem}")
                await browser.close()
                return "DRY-RUN-OK"
            btn = page.get_by_role("button", name="发布", exact=True).first
            await btn.click(timeout=15000)
            # 提交后验证：URL 跳转/成功提示 → 发布成功；校验 toast（请添加/请选择…）→ 如实报错
            outcome, detail = await _watch_submit(page)
            shot = await _shot(page, shot_dir, f"submit_{video_path.stem}_{datetime.now():%H%M%S}")
            await browser.close()
            if outcome != "ok":
                raise PublishError(f"提交未成功: {detail}", shot)
            return detail if detail.startswith("http") else page.url
        except Exception as e:
            if isinstance(e, (LoginExpiredError, PublishError)):
                raise
            shot = None
            try:
                shot = await _shot(page, shot_dir, f"fail_{video_path.stem}")
            except Exception:
                pass
            await browser.close()
            raise PublishError(f"发布失败: {e}", shot) from e
