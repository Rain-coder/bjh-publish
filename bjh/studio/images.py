# bjh/studio/images.py
import httpx
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
PALETTES = [((24, 30, 60), (90, 60, 160)), ((10, 60, 70), (20, 140, 130)),
            ((70, 20, 40), (170, 60, 90))]
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise FileNotFoundError("未找到系统中文字体，请安装 PingFang/黑体/Noto CJK")


def make_fallback_image(title: str, out_path: Path, palette_index: int = 0) -> Path:
    c1, c2 = PALETTES[palette_index % len(PALETTES)]
    img = Image.new("RGB", (W, H))
    for y in range(H):                      # 垂直渐变
        t = y / H
        img.paste(tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)), (0, y, W, y + 1))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(), 110)
    # 标题每行6字，居中大字
    line = title[:12]
    lines = [line[i:i + 6] for i in range(0, len(line), 6)]
    total_h = len(lines) * 150
    y0 = (H - total_h) // 2
    for i, ln in enumerate(lines):
        bbox = d.textbbox((0, 0), ln, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        d.text((x + 6, y0 + i * 150 + 6), ln, font=font, fill=(0, 0, 0))
        d.text((x, y0 + i * 150), ln, font=font, fill=(255, 255, 255))
    img.save(out_path)
    return out_path


async def fetch_pexels(query: str, count: int, out_dir: Path, api_key: str | None,
                       name_prefix: str = "pexels") -> list[Path]:
    if not api_key:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": api_key},
                             params={"query": query, "per_page": count, "orientation": "portrait"})
        r.raise_for_status()
        photos = r.json().get("photos", [])
        paths = []
        for i, ph in enumerate(photos[:count]):
            url = ph["src"]["portrait"]
            r2 = await client.get(url)
            r2.raise_for_status()
            data = r2.content
            p = out_dir / f"{name_prefix}_{i}.jpg"
            p.write_bytes(data)
            paths.append(p)
        return paths


async def collect_images(query: str, count: int, out_dir: Path,
                         api_key: str | None, titles: list[str],
                         queries: list[str] | None = None, generate=None,
                         gen_first: bool = False) -> list[Path]:
    """收集 count 张配图，槽位 i 与文案第 i 段对应。
    每槽取图优先级：gen_first=True 时 生成→搜图（always 模式），否则 搜图→生成（auto 模式）；
    全落空用兜底文字卡片。generate 为 async (prompt, out_path) -> Path|None。
    queries 提供时逐槽各搜 1 张（第 i 词对应第 i 段）；否则单一关键词搜一轮取不同图。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    single: list[Path] | None = None               # 关键词模式：一轮搜索结果惰性缓存

    async def _search_slot(i: int) -> Path | None:
        nonlocal single
        target = out_dir / f"img_{i}.jpg"
        if queries:
            q = queries[i] if i < len(queries) else queries[-1]
            try:
                got = await fetch_pexels(q, 1, out_dir, api_key, name_prefix=f"tmp_{i}")
            except Exception:
                return None
            if got:
                got[0].rename(target)
                return target
            return None
        if single is None:                          # 同词搜 count 张（保证不重复）
            try:
                single = await fetch_pexels(query, count, out_dir, api_key)
            except Exception:
                single = []
        if i < len(single):
            if single[i] != target:
                single[i].rename(target)
            return target
        return None

    paths: list[Path] = []
    for i in range(count):
        title = titles[i % len(titles)] if titles else query
        target = out_dir / f"img_{i}.jpg"
        img: Path | None = None
        if gen_first and generate is not None:      # always 模式：生成优先，失败才搜图
            img = await generate(title, target)
        if img is None:
            img = await _search_slot(i)
        if img is None and generate is not None:    # auto 模式：搜不到才生成
            img = await generate(title, target)
        if img is None:
            img = make_fallback_image(title, out_dir / f"fallback_{i}.png", palette_index=i)
        paths.append(img)
    return paths
