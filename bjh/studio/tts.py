# bjh/studio/tts.py
import asyncio
import re
from collections import namedtuple
from pathlib import Path

import edge_tts

Word = namedtuple("Word", ["offset_sec", "text"])
Sentence = namedtuple("Sentence", ["text", "start_sec", "end_sec"])
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])")


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]


def char_len(s: str) -> int:
    return len(re.sub(r"[\s，。！？!?…、：；''""“”‘’]", "", s))


def sentences_with_timing(words: list[Word], sentences: list[str]) -> list[Sentence]:
    result: list[Sentence] = []
    wi = 0
    prev_end = words[0].offset_sec if words else 0.0
    for sent in sentences:
        start = prev_end
        need, got = char_len(sent), 0
        while wi < len(words) and got < need:
            got += char_len(words[wi].text)
            wi += 1
        if wi < len(words):
            end = words[wi].offset_sec
        elif words:
            end = words[-1].offset_sec + 0.5
        else:
            end = start + 1.0
        end = max(end, start + 0.3)
        result.append(Sentence(sent, start, end))
        prev_end = end
    return result


async def synthesize(text: str, voice: str, out_mp3: Path, rate: str = "-10%") -> list[Word]:
    """edge-tts 合成，返回词级时间轴（offset 秒）。rate 如 "-10%"，默认接近自然口播语速。"""
    words: list[Word] = []
    # edge-tts>=7 默认发 SentenceBoundary；显式请求词级边界
    comm = edge_tts.Communicate(text, voice, boundary="WordBoundary", rate=rate)
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(Word(chunk["offset"] / 1e7, chunk["text"]))
    return words


async def synthesize_with_retry(text: str, voice: str, out_mp3: Path, retries: int = 3,
                                 rate: str = "-10%") -> list[Word]:
    for attempt in range(retries):
        try:
            return await synthesize(text, voice, out_mp3, rate=rate)
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("unreachable")
