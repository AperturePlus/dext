"""dext fetch bridge — aiohttp server + in-memory job queue + HumanFetcherBridge.

Implements the fixed userscript HTTP contract (overview §4). No DB, no LLM, no HTML
parsing. Public interface (spec §10) re-exported in Task 9.
"""
