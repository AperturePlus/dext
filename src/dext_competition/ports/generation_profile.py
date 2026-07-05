"""Competition generation-profile seam (grounded spec §7).

Competition owns its own ``generation_profile_version`` per operation
(qa / plan / assistant / query-understanding). Profiles live as operation
fragment files under ``data/competition/profiles/generation/``; the
integration owner assembles a read-only manifest. No shared mutable client.
"""
from __future__ import annotations

from dext_grounded import GenerationProfile, ProfileRegistry

__all__ = ["GenerationProfile", "ProfileRegistry"]
