# bjh/config.py
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class Config:
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_price_in: float = 2.0
    deepseek_price_out: float = 8.0
    pexels_api_key: str = ""
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    daily_limit: int = 8
    interval_min_min: int = 25
    interval_max_min: int = 50
    golden_hours: list[list[int]] = field(default_factory=lambda: [[7, 9], [12, 14], [18, 22]])
    sensitive_words: list[str] = field(default_factory=lambda: [
        "震惊", "惊呆", "绝对", "100%", "必看", "删前速看", "转发过万"])
    data_dir: Path = Path("data")


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    p = Path(path) if path else Path(DEFAULT_CONFIG_NAME)
    if p.exists():
        with open(p, "rb") as f:
            data = tomllib.load(f)
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    env = {
        "deepseek_api_key": "BJH_DEEPSEEK_API_KEY",
        "pexels_api_key": "BJH_PEXELS_API_KEY",
    }
    for attr, var in env.items():
        if os.environ.get(var):
            setattr(cfg, attr, os.environ[var])
    return cfg
