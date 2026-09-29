import shutil
import subprocess
from pathlib import Path
import pytest
from bjh.studio.tts import Sentence
from bjh.studio.subtitle import build_ass
from bjh.studio.images import make_fallback_image
from bjh.studio.compose import audio_duration, compose_video

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not ffmpeg or not shutil.which("ffprobe"),
                                reason="需要系统 ffmpeg/ffprobe")


def make_sine_mp3(path: Path, seconds: float = 3.0):
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)], check=True, capture_output=True)


def test_audio_duration(tmp_path):
    mp3 = tmp_path / "a.mp3"
    make_sine_mp3(mp3, 3.0)
    assert 2.8 < audio_duration(mp3) < 3.3


def test_compose_5s_sample(tmp_path):
    mp3 = tmp_path / "a.mp3"
    make_sine_mp3(mp3, 3.0)
    imgs = [make_fallback_image(f"画面{i}", tmp_path / f"img{i}.png", palette_index=i)
            for i in range(2)]
    sents = [Sentence("第一句字幕", 0.0, 1.5), Sentence("第二句字幕", 1.5, 3.0)]
    ass = tmp_path / "sub.ass"
    ass.write_text(build_ass(sents), encoding="utf-8")
    out = tmp_path / "out.mp4"
    compose_video(mp3, imgs, ass, out)
    assert out.exists() and out.stat().st_size > 10000
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "1080,1920"
    assert 2.8 < audio_duration(out) < 3.3
