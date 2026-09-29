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
            if is_login_url(page.url):
                shot = await _shot(page, shot_dir, f"login_expired_{video_path.stem}")
                raise LoginExpiredError(screenshot=shot)
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
                await _shot(page, shot_dir, f"dryrun_{video_path.stem}")
                await browser.close()
                return "DRY-RUN-OK"
            await page.click(SEL["submit_btn"])
            await page.wait_for_timeout(5000)
            await _shot(page, shot_dir, f"submit_{video_path.stem}")
            url = page.url
            await browser.close()
            return url
        except Exception as e:
            if isinstance(e, LoginExpiredError):
                raise
            shot = None
            try:
                shot = await _shot(page, shot_dir, f"fail_{video_path.stem}")
            except Exception:
                pass
            await browser.close()
            raise PublishError(f"发布失败: {e}", shot) from e
