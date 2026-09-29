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
