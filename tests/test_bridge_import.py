from pathlib import Path


def test_bridge_package_imports():
    import dext.bridge  # noqa: F401


def test_public_interface_is_reexported():
    import dext.bridge as b
    for name in ("HumanFetcherBridge", "FetchQueue", "FetchJob", "JobContext", "JobStatus",
                 "QueueStats", "DecisionCenter", "PendingDecision", "repair_mojibake_text",
                 "RedirectGuard", "RedirectVerdict", "classify_redirect", "create_app",
                 "run_server", "FetchResult"):
        assert hasattr(b, name), f"dext.bridge missing public export {name}"


def test_bridge_has_no_db_llm_or_page_imports():
    bridge_dir = Path(__file__).resolve().parent.parent / "src" / "dext" / "bridge"
    forbidden = ("sqlalchemy", "aiosqlite", "openai", "html2text", "beautifulsoup", "bs4",
                 "dext.storage", "dext.llm", "dext.page")
    for py in sorted(bridge_dir.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{py.name} must not reference {token}"
