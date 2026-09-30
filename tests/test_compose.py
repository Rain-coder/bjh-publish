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


def _grab_frame(video: Path, t: float, out: Path):
    subprocess.run([ffmpeg, "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(out)],
                   check=True, capture_output=True)


def _max_diff(a: Path, b: Path) -> int:
    """两帧降采样后的最大灰度差：libx264 对渐变的逐点量化噪声被平均掉，
    缩放运动/换图则保留（实测同段静态=0，跨段=23）。"""
    from PIL import Image, ImageChops
    d = ImageChops.difference(Image.open(a).convert("L").resize((32, 56)),
                              Image.open(b).convert("L").resize((32, 56)))
    return max(d.getextrema())


def test_compose_static_no_zoom(tmp_path):
    """回归：同段静止、段间 xfade 交叉溶解（seg=(3+0.5)/2=1.75s，转场 1.25~1.75s）。"""
    mp3 = tmp_path / "a.mp3"
    make_sine_mp3(mp3, 3.0)
    imgs = [make_fallback_image(f"画面{i}", tmp_path / f"img{i}.png", palette_index=i)
            for i in range(2)]
    out = tmp_path / "out.mp4"
    compose_video(mp3, imgs, None, out)
    _grab_frame(out, 0.4, tmp_path / "f1.png")     # 段 0 纯帧
    _grab_frame(out, 1.0, tmp_path / "f2.png")     # 段 0 纯帧（转场前 0.25s）
    _grab_frame(out, 1.5, tmp_path / "fm.png")     # 转场中点（两图混合）
    _grab_frame(out, 2.5, tmp_path / "f3.png")     # 段 1 纯帧
    assert _max_diff(tmp_path / "f1.png", tmp_path / "f2.png") <= 2   # 同段 → 静止
    assert _max_diff(tmp_path / "f1.png", tmp_path / "f3.png") > 10   # 换段 → 切图
    # 转场存在：中点帧是两图混合，与两端都有可见差异
    assert _max_diff(tmp_path / "fm.png", tmp_path / "f1.png") > 2
    assert _max_diff(tmp_path / "fm.png", tmp_path / "f3.png") > 2
