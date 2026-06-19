"""Shared HTTP-side-probe helpers (pure, no DB/LLM/page deps).

Used by ``redirect.py`` and ``probe.py`` so the compact exception-logging
formatting stays in one place (drift risk would otherwise hide headers/PII in
prod logs).
"""

from __future__ import annotations


def exception_summary(exc: Exception) -> str:
    """Compact, log-safe summary of a probe exception.

    Collapses ``TooManyRedirects.history`` to a count (the history entries
    contain verbose ``ClientResponse``/``CIMultiDictProxy`` reprs that must
    never reach logs) and truncates any other message to 160 chars.
    """
    error = type(exc).__name__
    if error == "TooManyRedirects":
        history = getattr(exc, "history", None)
        try:
            redirects = len(history) if history is not None else None
        except TypeError:
            redirects = None
        return (
            f"error={error} redirects={redirects}"
            if redirects is not None
            else f"error={error}"
        )

    message = " ".join(str(exc).split())
    if not message:
        return f"error={error}"
    if len(message) > 160:
        message = f"{message[:157]}..."
    return f"error={error} message={message}"
