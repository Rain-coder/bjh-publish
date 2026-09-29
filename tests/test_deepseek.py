import httpx
import pytest
import respx
from bjh.config import Config
from bjh.writer.deepseek import DeepSeekClient, estimate_cost

MSG = [{"role": "user", "content": "hi"}]


def _mock_response(respx_mock, status=200, content="ok", n=1):
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        httpx.Response(status, json={
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
