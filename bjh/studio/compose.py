# bjh/studio/compose.py
import shutil
import subprocess
from pathlib import Path

SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
XFADE_DUR = 0.5        # 相邻画面交叉溶解时长（秒）


def size_for(orientation: str) -> tuple[int, int]:
    """方向 → 画布 (宽, 高)；未知值按横屏处理。"""
    return SIZES.get(orientation, SIZES["landscape"])


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def compose_video(audio_path: Path, image_paths: list[Path], ass_path: Path | None,
                  out_path: Path, fps: int = 30, transition: str = "fade",
                  orientation: str = "landscape") -> Path:
    """每张图静态整屏，段落间 xfade 交叉溶解（总长严格等于音频），最后烧录 ASS 字幕。

    orientation 决定输出分辨率（横屏 1920x1080 / 竖屏 1080x1920）。
    转场重叠会吃掉 (n-1)*d 秒，故每段加长补偿：seg = (dur + (n-1)*d) / n；
    第 k 个转场的起点 offset = k*(seg-d)，逐级递增。"""
    assert image_paths, "至少需要一张图"
    W, H = size_for(orientation)
    dur = audio_duration(audio_path)
    n = len(image_paths)
    d = XFADE_DUR if n > 1 else 0.0
    seg = (dur + (n - 1) * d) / n if n > 1 else dur
    safe_ass: Path | None = None
    if ass_path:
        # subtitles= 滤镜路径若含 : 或空格需转义；复制为简单名规避
        out_path.parent.mkdir(parents=True, exist_ok=True)
        safe_ass = out_path.parent / "subs.ass"
        if ass_path.resolve() != safe_ass.resolve():
            shutil.copy(ass_path, safe_ass)
    cmd: list[str] = ["ffmpeg", "-y"]
    for img in image_paths:
        # loop 输入把每图扩成 seg 秒静态流；静态图无逐帧变换滤镜，帧数只由 -t 决定不膨胀
        cmd += ["-framerate", str(fps), "-loop", "1", "-t", f"{seg:.3f}", "-i", str(img)]
    cmd += ["-i", str(audio_path)]
    parts = []
    for i in range(n):
        # format=yuv420p：xfade 要求所有输入像素格式一致
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},fps={fps},setsar=1,format=yuv420p[v{i}]")
    if n == 1:
        chain = ";".join(parts)
        vmap = "[v0]"
    else:
        prev = "[v0]"
        for k in range(1, n):
            label = f"[x{k}]" if k < n - 1 else "[v]"
            parts.append(f"{prev}[v{k}]xfade=transition={transition}"
                         f":duration={d:.3f}:offset={k * (seg - d):.3f}{label}")
            prev = label
        chain = ";".join(parts)
        vmap = "[v]"
    if safe_ass:
        chain += f";{vmap}subtitles=filename='{safe_ass}'[vout]"
        vmap = "[vout]"
    cmd += ["-filter_complex", chain, "-map", vmap, "-map", f"{n}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-shortest", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{proc.stderr[-2000:]}")
    return out_path
