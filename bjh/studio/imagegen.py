# bjh/studio/imagegen.py
import base64
import httpx
from pathlib import Path
from bjh.config import Config


async def generate_image(prompt: str, out_path: Path, cfg: Config) -> Path | None:
    """OpenAI 兼容 /images/generations 文生图（默认智谱 CogView，可换任意兼容服务）。
    返回落盘路径；未配置 key 或失败返回 None（调用方走兜底卡片）。"""
    if not cfg.imagegen_api_key:
        return None
    payload = {"model": cfg.imagegen_model, "prompt": prompt, "size": cfg.imagegen_size}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{cfg.imagegen_base_url.rstrip('/')}/images/generations",
                headers={"Authorization": f"Bearer {cfg.imagegen_api_key}"}, json=payload)
            r.raise_for_status()
            items = r.json().get("data") or []
            if not items:
                return None
            item = items[0]
            if item.get("b64_json"):                 # OpenAI 风格内联返回
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(base64.b64decode(item["b64_json"]))
                return out_path
            url = item.get("url")                    # 智谱/兼容服务返回 URL
            if not url:
                return None
            r2 = await client.get(url)
            r2.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r2.content)
            return out_path
    except Exception:
        return None
