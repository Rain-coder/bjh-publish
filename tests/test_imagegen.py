import base64
import httpx
import pytest
import respx
from bjh.config import Config
from bjh.studio.imagegen import generate_image

GEN_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"


async def test_generate_image_no_key_returns_none(tmp_path):
    assert await generate_image("一个年轻人", tmp_path / "g.jpg", Config()) is None


@respx.mock
async def test_generate_image_downloads_url(tmp_path, respx_mock):
    respx_mock.post(GEN_URL).mock(httpx.Response(200, json={
        "created": 1, "data": [{"url": "https://img.example/gen.png"}]}))
    respx_mock.get("https://img.example/gen.png").mock(
        httpx.Response(200, content=b"\x89PNG fake"))
    cfg = Config(imagegen_api_key="k")
    p = await generate_image("画面", tmp_path / "g.png", cfg)
    assert p == tmp_path / "g.png" and p.read_bytes().startswith(b"\x89PNG")
    req = respx_mock.calls[0].request                     # 第一个调用是生成 POST
    assert req.headers["Authorization"] == "Bearer k"
    assert "cogview-3-plus" in req.read().decode()      # 默认模型走配置


@respx.mock
async def test_generate_image_b64_inline(tmp_path, respx_mock):
    raw = b"\xff\xd8fakejpg"
    respx_mock.post(GEN_URL).mock(httpx.Response(200, json={
        "data": [{"b64_json": base64.b64encode(raw).decode()}]}))
    p = await generate_image("画面", tmp_path / "g.jpg", Config(imagegen_api_key="k"))
    assert p is not None and p.read_bytes() == raw


@respx.mock
async def test_generate_image_error_returns_none(tmp_path, respx_mock):
    respx_mock.post(GEN_URL).mock(httpx.Response(500, content="boom"))
    assert await generate_image("画面", tmp_path / "g.jpg", Config(imagegen_api_key="k")) is None
