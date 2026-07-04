from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from aiohttp.abc import AbstractAccessLogger

from dext_recommend.api.keys import REQUEST_ID_KEY


class RecommendAccessLogger(AbstractAccessLogger):
    """Human-readable aiohttp access logs for local API debugging."""

    @property
    def enabled(self) -> bool:
        return self.logger.isEnabledFor(logging.INFO)

    def log(self, request: Any, response: Any, time: float) -> None:
        remote = getattr(request, "remote", None) or "-"
        method = getattr(request, "method", "-")
        path_qs = getattr(request, "path_qs", None) or getattr(request, "rel_url", "-")
        version = getattr(request, "version", None)
        major = getattr(version, "major", 1)
        minor = getattr(version, "minor", 1)
        status = int(getattr(response, "status", 0) or 0)
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = "-"
        try:
            request_id = request.get(REQUEST_ID_KEY, "-")
        except AttributeError:
            request_id = "-"
        self.logger.info(
            '%s - "%s %s HTTP/%s.%s" %s %s %.1fms request_id=%s',
            remote,
            method,
            path_qs,
            major,
            minor,
            status,
            phrase,
            time * 1000,
            request_id or "-",
        )


__all__ = ["RecommendAccessLogger"]
