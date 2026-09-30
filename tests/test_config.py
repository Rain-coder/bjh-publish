from pathlib import Path
from bjh.config import load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 无 config.toml
    cfg = load_config(None)
    assert cfg.daily_limit == 8
    assert cfg.interval_min_min == 25 and cfg.interval_max_min == 50
    assert cfg.golden_hours == [[7, 9], [12, 14], [18, 22]]
    assert "震惊" in cfg.sensitive_words
    assert cfg.orientation == "landscape"          # 默认横屏 1920x1080


def test_load_toml_and_env_override(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'deepseek_api_key = "toml-key"\ndaily_limit = 5\n', encoding="utf-8"
    )
    monkeypatch.setenv("BJH_DEEPSEEK_API_KEY", "env-key")
    cfg = load_config(str(tmp_path / "config.toml"))
    assert cfg.deepseek_api_key == "env-key"   # 环境变量优先
    assert cfg.daily_limit == 5                # toml 次之


def test_save_config_roundtrip_keeps_lists(tmp_path):
    from bjh.config import save_config
    cfg = load_config(None)
    cfg.daily_limit = 5
    cfg.golden_hours = [[6, 9], [20, 23]]
    cfg.sensitive_words = ["震惊", "删前速看", "必看"]
    p = str(tmp_path / "config.toml")
    save_config(cfg, p)
    cfg2 = load_config(p)
    assert cfg2.daily_limit == 5
    assert cfg2.golden_hours == [[6, 9], [20, 23]]   # F4: 不再丢失
    assert cfg2.sensitive_words == ["震惊", "删前速看", "必看"]
