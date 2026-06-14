from dext.bridge.mojibake import repair_mojibake_text


def test_repairs_utf8_decoded_as_latin1():
    original = "中文教师"
    mojibake = original.encode("utf-8").decode("latin-1")
    assert mojibake != original
    assert repair_mojibake_text(mojibake) == original


def test_repairs_within_html():
    html = "<h1>计算机学院</h1>"
    assert repair_mojibake_text(html.encode("utf-8").decode("latin-1")) == html


def test_leaves_correct_utf8_untouched():
    for s in ("中文教师", "Hello world", "", "教授 Zhang San"):
        assert repair_mojibake_text(s) == s


def test_leaves_legit_latin1_text_untouched():
    # 'café' encodes to latin-1 but b'caf\xe9' is invalid utf-8 → returned unchanged.
    assert repair_mojibake_text("café") == "café"
