from pathlib import Path
from bjh.config import load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 无 config.toml
    cfg = load_config(None)
    assert cfg.daily_limit == 8
    assert cfg.interval_min_min == 25 and cfg.interval_max_min == 50
    assert cfg.golden_hours == [[7, 9], [12, 14], [18, 22]]
    assert "震惊" in cfg.sensitive_words


def test_load_toml_and_env_override(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'deepseek_api_key = "toml-key"\ndaily_limit = 5\n', encoding="utf-8"
    )
    monkeypatch.setenv("BJH_DEEPSEEK_API_KEY", "env-key")
    cfg = load_config(str(tmp_path / "config.toml"))
    assert cfg.deepseek_api_key == "env-key"   # 环境变量优先
    assert cfg.daily_limit == 5                # toml 次之
