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


@respx.mock
@needs_font
async def test_collect_images_falls_back_on_image_404(tmp_path, respx_mock):
    respx_mock.get("https://api.pexels.com/v1/search").mock(httpx.Response(200, json={
        "photos": [{"src": {"portrait": "https://img.example/1.jpg"}}]}))
    respx_mock.get("https://img.example/1.jpg").mock(httpx.Response(404, content=b"not found"))
    paths = await collect_images("职场", 1, tmp_path, "fake-key", titles=["标题A"])
    assert len(paths) == 1 and paths[0].exists()
    assert paths[0].name == "fallback_0.png"


@respx.mock
@needs_font
async def test_collect_images_per_query_search(tmp_path, respx_mock):
    """queries 模式：第 i 个搜索词对应第 i 张图，逐词各搜 1 张；某词落空仅该槽兜底。"""
    respx_mock.get("https://api.pexels.com/v1/search",
                   params={"query": "office worker", "per_page": 1,
                           "orientation": "portrait"}).mock(httpx.Response(200, json={
        "photos": [{"src": {"portrait": "https://img.example/a.jpg"}}]}))
    respx_mock.get("https://api.pexels.com/v1/search",
                   params={"query": "crying baby", "per_page": 1,
                           "orientation": "portrait"}).mock(httpx.Response(200, json={
        "photos": []}))
    respx_mock.get("https://img.example/a.jpg").mock(
        httpx.Response(200, content=b"\xff\xd8fakejpg"))
    paths = await collect_images("职场", 2, tmp_path, "fake-key",
                                 titles=["第一段", "第二段"],
                                 queries=["office worker", "crying baby"])
    assert len(paths) == 2
    assert paths[0].name == "img_0.jpg" and paths[0].read_bytes().startswith(b"\xff\xd8")
    assert paths[1].name == "fallback_1.png" and paths[1].exists()


def _fake_generate(record, ok=True):
    from PIL import Image

    async def generate(prompt, out_path):
        record.append(prompt)
        if not ok:
            return None
        Image.new("RGB", (100, 100), (200, 30, 30)).save(out_path)
        return out_path
    return generate


@respx.mock
@needs_font
async def test_collect_images_generates_when_search_empty(tmp_path, respx_mock):
    """auto 模式：搜图落空的槽位调用文生图，提示词=对应段落文字。"""
    respx_mock.get("https://api.pexels.com/v1/search").mock(
        httpx.Response(200, json={"photos": []}))
    record: list[str] = []
    paths = await collect_images("职场", 2, tmp_path, "fake-key",
                                 titles=["第一段文字", "第二段文字"],
                                 queries=["office worker", "crying baby"],
                                 generate=_fake_generate(record))
    assert len(paths) == 2 and all(p.name == "img_0.jpg" or p.name == "img_1.jpg"
                                   for p in paths)
    assert record == ["第一段文字", "第二段文字"]


@respx.mock
@needs_font
async def test_collect_images_gen_first_skips_search(tmp_path, respx_mock):
    """always 模式（gen_first）：生成成功即不搜图。"""
    record: list[str] = []
    paths = await collect_images("职场", 2, tmp_path, "fake-key",
                                 titles=["第一段", "第二段"],
                                 generate=_fake_generate(record), gen_first=True)
    assert len(paths) == 2 and record == ["第一段", "第二段"]
    assert respx_mock.calls == []                    # 未发起任何 Pexels 请求


@respx.mock
@needs_font
async def test_collect_images_generate_fail_falls_to_card(tmp_path, respx_mock):
    respx_mock.get("https://api.pexels.com/v1/search").mock(
        httpx.Response(200, json={"photos": []}))
    paths = await collect_images("职场", 1, tmp_path, "fake-key", titles=["第一段"],
                                 generate=_fake_generate([], ok=False))
    assert paths[0].name == "fallback_0.png"
