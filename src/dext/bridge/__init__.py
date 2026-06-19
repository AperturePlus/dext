"""dext fetch bridge — aiohttp server + in-memory job queue + HumanFetcherBridge.

Implements the fixed userscript HTTP contract (overview §4). No DB, no LLM, no HTML
parsing. Public interface (spec §10) re-exported below.
"""

from dext.bridge.decision import DecisionCenter, PendingDecision
from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.mojibake import repair_mojibake_text
from dext.bridge.queue import FetchJob, FetchQueue, JobContext, JobStatus, QueueStats
from dext.bridge.probe import StatusProbe, StatusVerdict, classify_status
from dext.bridge.redirect import RedirectGuard, RedirectVerdict, classify_redirect
from dext.bridge.server import create_app, run_server
from dext.types import FetchResult

__all__ = [
    "HumanFetcherBridge",
    "FetchQueue",
    "FetchJob",
    "JobContext",
    "JobStatus",
    "QueueStats",
    "DecisionCenter",
    "PendingDecision",
    "repair_mojibake_text",
    "RedirectGuard",
    "RedirectVerdict",
    "classify_redirect",
    "StatusProbe",
    "StatusVerdict",
    "classify_status",
    "create_app",
    "run_server",
    "FetchResult",
]
