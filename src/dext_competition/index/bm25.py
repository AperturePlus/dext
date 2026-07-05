"""Deterministic, dependency-free BM25 index for the competition corpus."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any

from dext_competition.contracts.knowledge import Chunk

FORMAT_VERSION = "competition-bm25-v1"
TOKENIZER_VERSION = "competition-cjk-bigram-v1"
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

_SEGMENT_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


class BM25FormatError(ValueError):
    """Serialized BM25 data is malformed or incompatible."""


def tokenize(text: str) -> tuple[str, ...]:
    """Versioned tokenizer: normalized ASCII words plus CJK unigrams/bigrams."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in _SEGMENT_RE.finditer(normalized):
        segment = match.group(0)
        if not _CJK_RE.fullmatch(segment):
            tokens.append(segment)
            continue
        chars = list(segment)
        tokens.extend(chars)
        tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class BM25Index:
    """Immutable postings index whose document order matches ``chunks.jsonl``."""

    document_hashes: tuple[str, ...]
    document_lengths: tuple[int, ...]
    postings: dict[str, tuple[tuple[int, int], ...]]
    average_document_length: float
    k1: float = DEFAULT_K1
    b: float = DEFAULT_B

    @classmethod
    def from_chunks(cls, chunks: tuple[Chunk, ...]) -> "BM25Index":
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for document_id, chunk in enumerate(chunks):
            counts = Counter(tokenize(f"{chunk.heading_path}\n{chunk.text}"))
            lengths.append(sum(counts.values()))
            for token, frequency in counts.items():
                postings[token].append((document_id, frequency))
        average = sum(lengths) / len(lengths) if lengths else 0.0
        return cls(
            document_hashes=tuple(chunk.chunk_hash for chunk in chunks),
            document_lengths=tuple(lengths),
            postings={
                token: tuple(rows) for token, rows in sorted(postings.items())
            },
            average_document_length=average,
        )

    def score(self, text: str) -> dict[int, float]:
        """Return positive BM25 scores keyed by document ordinal."""

        query_tokens = sorted(set(tokenize(text)))
        if not query_tokens or not self.document_hashes:
            return {}
        document_count = len(self.document_hashes)
        average_length = self.average_document_length or 1.0
        scores: dict[int, float] = defaultdict(float)
        for token in query_tokens:
            rows = self.postings.get(token, ())
            document_frequency = len(rows)
            if not document_frequency:
                continue
            inverse_document_frequency = math.log(
                1.0
                + (document_count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            for document_id, term_frequency in rows:
                length = self.document_lengths[document_id]
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * length / average_length
                )
                scores[document_id] += inverse_document_frequency * (
                    term_frequency * (self.k1 + 1.0) / denominator
                )
        return dict(scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "average_document_length": self.average_document_length,
            "b": self.b,
            "document_count": len(self.document_hashes),
            "document_hashes": list(self.document_hashes),
            "document_lengths": list(self.document_lengths),
            "format_version": FORMAT_VERSION,
            "k1": self.k1,
            "postings": {
                token: [[document_id, frequency] for document_id, frequency in rows]
                for token, rows in sorted(self.postings.items())
            },
            "tokenizer_version": TOKENIZER_VERSION,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "BM25Index":
        try:
            if not isinstance(value, dict):
                raise TypeError("root must be an object")
            if value["format_version"] != FORMAT_VERSION:
                raise ValueError("unsupported format_version")
            if value["tokenizer_version"] != TOKENIZER_VERSION:
                raise ValueError("unsupported tokenizer_version")
            if not isinstance(value["document_hashes"], list) or not all(
                isinstance(item, str) for item in value["document_hashes"]
            ):
                raise TypeError("document_hashes must be a string array")
            hashes = tuple(value["document_hashes"])
            lengths = tuple(int(item) for item in value["document_lengths"])
            if value["document_count"] != len(hashes) or len(lengths) != len(hashes):
                raise ValueError("document count mismatch")
            if any(length < 0 for length in lengths):
                raise ValueError("negative document length")
            postings_value = value["postings"]
            if not isinstance(postings_value, dict):
                raise TypeError("postings must be an object")
            postings: dict[str, tuple[tuple[int, int], ...]] = {}
            for token, raw_rows in postings_value.items():
                rows = tuple((int(row[0]), int(row[1])) for row in raw_rows)
                if any(
                    document_id < 0
                    or document_id >= len(hashes)
                    or frequency < 1
                    for document_id, frequency in rows
                ):
                    raise ValueError("invalid posting")
                if tuple(sorted(rows)) != rows:
                    raise ValueError("postings are not ordered")
                postings[str(token)] = rows
            average = float(value["average_document_length"])
            k1 = float(value["k1"])
            b = float(value["b"])
            if not math.isfinite(average) or average < 0:
                raise ValueError("invalid average document length")
            expected_average = sum(lengths) / len(lengths) if lengths else 0.0
            if not math.isclose(average, expected_average, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("average document length mismatch")
            if not math.isfinite(k1) or k1 <= 0 or not (0 <= b <= 1):
                raise ValueError("invalid BM25 parameters")
            return cls(hashes, lengths, postings, average, k1, b)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise BM25FormatError(f"invalid BM25 artifact: {exc}") from exc


__all__ = [
    "BM25FormatError",
    "BM25Index",
    "DEFAULT_B",
    "DEFAULT_K1",
    "FORMAT_VERSION",
    "TOKENIZER_VERSION",
    "tokenize",
]
