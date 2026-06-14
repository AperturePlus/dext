"""Best-effort repair of UTF-8 bytes that were mis-decoded as Latin-1.

Safe by construction (see repair_mojibake_text). Forward UTF-8 reading and
`ensure_ascii=False` JSON output handle the rest of the UTF-8 invariant (overview §6).
"""

from __future__ import annotations


def _suspicion(text: str) -> int:
    """Count Latin-1 high bytes (U+0080–U+00FF): abundant in UTF-8-as-Latin-1
    mojibake, absent from correctly-decoded CJK."""
    return sum(1 for ch in text if 0x80 <= ord(ch) <= 0xFF)


def repair_mojibake_text(text: str) -> str:
    """Repair e.g. 'ä¸­æ–‡' → '中文'.

    Safe: text containing real CJK can't .encode('latin-1') (raises → returned
    untouched, so correct UTF-8 is never harmed); non-mojibake Latin-1 fails the
    utf-8 round-trip decode (raises → returned untouched, so 'café' survives).
    The repair is accepted only when it does not increase the high-byte suspicion
    count, guarding against rare double-repair / false positives.
    """
    if not text:
        return text
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text
    try:
        repaired = raw.decode("utf-8")
    except UnicodeDecodeError:
        return text
    if repaired == text or _suspicion(repaired) > _suspicion(text):
        return text
    return repaired
