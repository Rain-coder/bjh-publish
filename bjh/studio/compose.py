# bjh/studio/compose.py
import shutil
import subprocess
from pathlib import Path

W, H = 1080, 1920


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def compose_video(audio_path: Path, image_paths: list[Path], ass_path: Path | None,
                  out_path: Path, fps: int = 30) -> Path:
    """每张图占 audio_duration/len(images) 秒，zoompan Ken Burns 缓放，concat 后烧录 ASS 字幕。"""
    assert image_paths, "至少需要一张图"
    dur = audio_duration(audio_path)
    seg = dur / len(image_paths)
    safe_ass: Path | None = None
    if ass_path:
        # subtitles= 滤镜路径若含 : 或空格需转义；复制为简单名规避
        out_path.parent.mkdir(parents=True, exist_ok=True)
        safe_ass = out_path.parent / "subs.ass"
        if ass_path.resolve() != safe_ass.resolve():
            shutil.copy(ass_path, safe_ass)
    cmd: list[str] = ["ffmpeg", "-y"]
    for img in image_paths:
        cmd += ["-loop", "1", "-t", f"{seg:.3f}", "-i", str(img)]
    cmd += ["-i", str(audio_path)]
    n = len(image_paths)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"zoompan=z='min(zoom+0.0006,1.2)':d={int(seg * fps)}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},setsar=1[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(n))
    chain = ";".join(parts) + f";{concat_in}concat=n={n}:v=1:a=0[v]"
    vmap = "[v]"
    if safe_ass:
        chain += f";[v]subtitles=filename='{safe_ass}'[vout]"
        vmap = "[vout]"
    cmd += ["-filter_complex", chain, "-map", vmap, "-map", f"{n}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-shortest", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{proc.stderr[-2000:]}")
    return out_path
