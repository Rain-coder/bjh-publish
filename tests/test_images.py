from pathlib import Path
import pytest
import httpx
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
    respx_mock.get("https://api.pexels.com/v1/search").mock(httpx.Response(200, json={
        "photos": [{"src": {"portrait": "https://img.example/1.jpg"}}]}))
    respx_mock.get("https://img.example/1.jpg").mock(
        httpx.Response(200, content=b"\xff\xd8fakejpg"))
    from bjh.studio.images import fetch_pexels
    paths = await fetch_pexels("职场", 1, tmp_path, "fake-key")
    assert len(paths) == 1 and paths[0].read_bytes().startswith(b"\xff\xd8")
