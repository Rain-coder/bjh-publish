from bjh.studio.tts import Sentence
from bjh.studio.subtitle import wrap_line, ass_time, build_ass


def test_wrap_line():
    assert wrap_line("短句", 15) == ["短句"]
    long = "这一句话特别长需要被切成两行显示才不会超出屏幕"
    lines = wrap_line(long, 15)
    assert len(lines) == 2 and all(len(l) <= 15 for l in lines)


def test_ass_time():
    assert ass_time(1.5) == "0:00:01.50"
    assert ass_time(3661.25) == "1:01:01.25"


def test_build_ass_structure():
    sents = [Sentence("第一句", 0.0, 2.0), Sentence("第二句超长超长超长超长超长超长超长超长", 2.0, 5.0)]
    ass = build_ass(sents)
    assert "[Script Info]" in ass and "1080" in ass and "1920" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,第一句" in ass
    assert "\\N" in ass          # 长句换行
