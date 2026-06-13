"""dext storage — schema, DB lifecycle, single-writer DB worker, dedup.

The only write path in the system. Pure data layer: no network, no LLM,
no HTML parsing (source doc §5/§9/§13/§14, overview §2/§7).
"""
