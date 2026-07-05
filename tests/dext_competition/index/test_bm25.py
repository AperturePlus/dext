from __future__ import annotations

import hashlib

import pytest

from dext_competition import Chunk
from dext_competition.index.bm25 import (
    BM25FormatError,
    BM25Index,
    TOKENIZER_VERSION,
    tokenize,
)


def _chunk(path: str, heading: str, text: str) -> Chunk:
    return Chunk(path, heading, hashlib.sha256(text.encode()).hexdigest(), text)


def test_tokenizer_is_nfkc_casefolded_and_emits_cjk_unigrams_bigrams():
    tokens = tokenize("ＡI Math 数学建模")
    assert "ai" in tokens
    assert "math" in tokens
    assert {"数", "学", "数学", "建模"}.issubset(tokens)


def test_bm25_prefers_chunk_with_more_specific_terms():
    chunks = (
        _chunk("a.md", "a > 数学建模", "数学建模竞赛与论文写作"),
        _chunk("b.md", "b > 数学", "大学数学基础"),
    )
    index = BM25Index.from_chunks(chunks)
    scores = index.score("数学建模")
    assert scores[0] > scores[1]


def test_bm25_round_trip_is_deterministic_and_versioned():
    index = BM25Index.from_chunks((_chunk("a.md", "a", "机器人 AI"),))
    restored = BM25Index.from_dict(index.to_dict())
    assert restored == index
    assert restored.to_dict()["tokenizer_version"] == TOKENIZER_VERSION


def test_bm25_rejects_unknown_tokenizer_version():
    value = BM25Index.from_chunks((_chunk("a.md", "a", "text"),)).to_dict()
    value["tokenizer_version"] = "unknown"
    with pytest.raises(BM25FormatError):
        BM25Index.from_dict(value)
