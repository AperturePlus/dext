import dext.llm as llm


def test_public_interface_importable():
    names = [
        "LLMClient", "LLMResponse",
        "decide_links", "Decision", "DecidedLink", "DeciderNode", "DeciderContext",
        "extract_professors", "ExtractionResult", "OrgUnitContext",
        "sanitize", "assess_no_data", "NoDataVerdict",
        "build_decider_messages", "build_extractor_messages",
        "SAVE_PROFESSORS_TOOL", "prompt_hash", "PROMPT_HASHES", "truncate_to_budget",
    ]
    for name in names:
        assert hasattr(llm, name), f"missing public export: {name}"
    assert set(llm.__all__) == set(names)
