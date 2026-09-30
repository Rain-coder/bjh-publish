# bjh/config.py
import json
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
    # 语速：edge-tts 默认偏快（~5字/秒新闻腔），-10% ≈ 4.5字/秒 接近自然口播
    tts_rate: str = "-10%"
    # 文生图兜底（配图搜不到时）：OpenAI 兼容 /images/generations 接口
    imagegen_api_key: str = ""
    imagegen_base_url: str = "https://open.bigmodel.cn/api/paas/v4"   # 智谱 CogView
    imagegen_model: str = "cogview-3-plus"
    imagegen_size: str = "768x1344"                                    # 竖版 9:16
    imagegen_mode: str = "auto"        # auto=搜不到才生成 | always=全部生成 | off
    # 视频方向：landscape=横屏 1920x1080 | portrait=竖屏 1080x1920
    orientation: str = "landscape"
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
        "imagegen_api_key": "BJH_IMAGEGEN_API_KEY",
    }
    for attr, var in env.items():
        if os.environ.get(var):
            setattr(cfg, attr, os.environ[var])
    return cfg


_SAVE_FIELDS = ("deepseek_api_key", "deepseek_base_url", "deepseek_model", "pexels_api_key",
                "tts_voice", "tts_rate", "imagegen_api_key", "imagegen_base_url",
                "imagegen_model", "imagegen_size", "imagegen_mode", "orientation",
                "daily_limit", "interval_min_min", "interval_max_min")


_LIST_FIELDS = ("golden_hours", "sensitive_words")  # 列表字段，JSON 数组即合法 TOML


def save_config(cfg: Config, path: str | None = None) -> None:
    p = Path(path or DEFAULT_CONFIG_NAME)
    lines = [f'{f} = "{getattr(cfg, f)}"' if isinstance(getattr(cfg, f), str)
             else f"{f} = {getattr(cfg, f)}" for f in _SAVE_FIELDS]
    # 列表字段必须保存，否则面板改设置会静默清空敏感词/黄金时段
    lines += [f"{f} = {json.dumps(getattr(cfg, f), ensure_ascii=False)}"
              for f in _LIST_FIELDS]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
