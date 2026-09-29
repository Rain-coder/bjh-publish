# bjh/studio/subtitle.py
import re
from bjh.studio.tts import Sentence

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fs},&H00FFFFFF,&H00000000,1,4,1,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Text
"""


def wrap_line(text: str, max_chars: int = 15) -> list[str]:
    text = re.sub(r"\s+", "", text)
    lines = [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1][:max_chars - 1] + "…"
    return lines


def ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(sentences: list[Sentence], font_size: int = 76, width: int = 1080,
              height: int = 1920, font: str = "Hiragino Sans GB") -> str:
    # 字体须为 fontconfig 可见名（brew ffmpeg/libass 不识别 macOS 隐藏的 PingFang，
    # 2026-09 实测 Hiragino Sans GB 在 fc-list :lang=zh 中，否则字幕烧成豆腐块）
    out = [ASS_HEADER.format(width=width, height=height, font=font, fs=font_size)]
    for s in sentences:
        text = "\\N".join(wrap_line(s.text))
        out.append(f"Dialogue: 0,{ass_time(s.start_sec)},{ass_time(s.end_sec)},Default,{text}")
    return "\n".join(out) + "\n"
