import pytest
from bjh.studio.tts import Word, split_sentences, char_len, sentences_with_timing


def test_split_sentences():
    text = "月薪三万够不够花？很多人说不够。但真相是什么！你绝对想不到…"
    parts = split_sentences(text)
    assert len(parts) == 4 and parts[0].endswith("？")


def test_char_len_ignores_punct():
    assert char_len("你好，世界。") == 4


def test_sentences_with_timing_aligns_by_charcount():
    sents = ["第一句话共六字", "第二句短些"]
    # 8 个 word，每词 3 字（char_len=3），offset 从 0.0 起步长 0.5s
    words = [Word(0.0 + i * 0.5, f"词{i}番") for i in range(8)]
    out = sentences_with_timing(words, sents)
    assert out[0].text == "第一句话共六字"
    assert out[0].start_sec == 0.0
    # 第一句 need=7：消耗 3 个词(got=9) → 结束于下一词 offset=1.5
    assert out[0].end_sec == pytest.approx(1.5)
    assert out[1].start_sec == 1.5
    # 无论词是否用尽，句尾都保证 ≥ start+0.3
    assert out[-1].end_sec >= out[-1].start_sec + 0.3
