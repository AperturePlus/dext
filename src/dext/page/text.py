"""HTML → clean text snapshot (for the SP5 LLM) and content hashing (spec §3).

html2text config (spec §3): no hard wrapping (body_width=0), ignore images,
ignore link URLs (anchor text is kept; URLs are captured separately in
link_signals), keep Unicode (unicode_snob) so Chinese is preserved.
"""

from __future__ import annotations

import hashlib

import html2text


def html_to_text(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0          # no hard line wrapping
    converter.ignore_images = True
    converter.ignore_links = True     # keep anchor text, drop URL noise
    converter.ignore_emphasis = True  # drop **/_ markers for cleaner text
    converter.unicode_snob = True     # keep Chinese rather than ASCII approximations
    return converter.handle(html or "").strip()


def content_hash(raw_html: str) -> str:
    """sha256 of the raw HTML as UTF-8 bytes (overview §6) — used to detect
    content change and avoid duplicate extraction."""
    return hashlib.sha256((raw_html or "").encode("utf-8")).hexdigest()
