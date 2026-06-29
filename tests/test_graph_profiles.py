import hashlib

from dext_graph.models import SourceProfessor
from dext_graph.profiles import build_profile, normalize_text, prefixed_input


class CharacterTokenizer:
    identity = "character-v1"

    def encode(self, text, *, add_special_tokens=False):
        values = [ord(char) for char in text]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, token_ids):
        return "".join(chr(value) for value in token_ids if value > 2)


def _professor(**overrides):
    values = {
        "source_id": 7,
        "source_row_key": "hash:professors:7",
        "point_id": "5f763643-e0f2-5993-8944-00c1c29e30b7",
        "name": "绝不能进入向量的姓名",
        "university": "测试大学",
        "org_units": ("计算机学院",),
        "title": "教授",
        "research_areas": "图神经网络" * 1000,
        "publications": "代表论文" * 1000,
        "bio": "个人简介" * 1000,
    }
    values.update(overrides)
    return SourceProfessor(**values)


def test_profile_is_deterministic_bounded_and_excludes_name():
    tokenizer = CharacterTokenizer()
    first = build_profile(_professor(), "baseline-v1", tokenizer, max_tokens=200)
    second = build_profile(_professor(), "baseline-v1", tokenizer, max_tokens=200)
    assert first == second
    assert first.token_count <= 200
    assert "绝不能进入向量的姓名" not in first.normalized_profile
    assert first.normalized_profile.startswith("学校：测试大学")
    expected = hashlib.sha256(
        f"baseline-v1\n{first.normalized_profile}".encode("utf-8")
    ).hexdigest()
    assert first.profile_hash == expected


def test_profile_skips_empty_semantic_content():
    result = build_profile(
        _professor(research_areas=None, publications=None, bio=None),
        "baseline-v1",
        CharacterTokenizer(),
    )
    assert result is None


def test_controlled_templates_apply_their_research_field_budgets():
    tokenizer = CharacterTokenizer()
    professor = _professor(research_areas="研" * 5000, publications=None, bio=None)
    baseline = build_profile(professor, "baseline-v1", tokenizer, max_tokens=4096)
    research_heavy = build_profile(
        professor, "research-heavy-v1", tokenizer, max_tokens=4096
    )
    baseline_research = baseline.normalized_profile.split("研究方向原文：", 1)[1]
    heavy_research = research_heavy.normalized_profile.split("研究方向原文：", 1)[1]
    assert len(baseline_research) == 2048
    assert len(heavy_research) == 2560


def test_normalization_and_prefix_limit():
    tokenizer = CharacterTokenizer()
    assert normalize_text("Ａ  B\r\n\r\n C") == "A B\nC"
    assert prefixed_input("内容", "passage: ", tokenizer, max_tokens=20) == "passage: 内容"
