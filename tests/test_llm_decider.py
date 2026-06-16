from dext.llm.decider import Decision, DecidedLink, _parse_decision
from dext.engine.names import clean_org_unit_name
from dext.page.links import LinkSignal


def _sig(url):
    return LinkSignal(href=url, url=url, anchor_text="张三", heading=None,
                      parent_class=None, path_segments=["teacher"], same_site=True)


def test_parse_keeps_only_candidate_urls():
    cands = [_sig("https://x/a"), _sig("https://x/b")]
    raw = ('{"links": ['
           '{"url": "https://x/a", "label": "detail", "confidence": 0.9, "is_leaf": true},'
           '{"url": "https://x/HALLUCINATED", "label": "detail", "confidence": 1.0, "is_leaf": true}'
           '], "page_is_leaf": false}')
    d = _parse_decision(raw, cands)
    assert isinstance(d, Decision)
    assert [l.url for l in d.links] == ["https://x/a"]
    assert d.links[0] == DecidedLink(url="https://x/a", label="detail", confidence=0.9, is_leaf=True)


def test_parse_captures_college_org_unit_name():
    cands = [_sig("https://x/a")]
    raw = ('{"links": ['
           '{"url": "https://x/a", "label": "college", "confidence": 0.8, "is_leaf": false, '
           '"org_unit_name": "数学学院（信息与计算科学系"}'
           '], "page_is_leaf": false}')
    d = _parse_decision(raw, cands)
    assert d.links[0].org_unit_name == "数学学院（信息与计算科学系"


def test_clean_org_unit_name_fixes_unclosed_parenthesis():
    assert clean_org_unit_name(" 数学学院 （信息与计算科学系 ") == "数学学院（信息与计算科学系）"


def test_parse_maps_unknown_label_to_noise_and_coerces_bad_confidence():
    cands = [_sig("https://x/a")]
    raw = '{"links": [{"url": "https://x/a", "label": "weird", "confidence": "NaNish"}]}'
    d = _parse_decision(raw, cands)
    assert d.links[0].label == "noise"
    assert d.links[0].confidence == 0.0
    assert d.links[0].is_leaf is False


def test_parse_invalid_json_sets_error():
    d = _parse_decision("{not json", [_sig("https://x/a")])
    assert d.links == []
    assert d.parse_error is not None
    assert "invalid_json" in d.parse_error


def test_parse_page_is_leaf_flag():
    d = _parse_decision('{"links": [], "page_is_leaf": true}', [])
    assert d.page_is_leaf is True


def test_parse_keeps_valid_link_exclusion_reason():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "noise", "confidence": 0.9, '
           '"is_leaf": false, "exclusion_reason": "retired"}], "page_is_leaf": false}')
    d = _parse_decision(raw, cands)
    assert d.links[0].exclusion_reason == "retired"


def test_parse_nulls_invalid_link_exclusion_reason():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "noise", "confidence": 0.9, '
           '"is_leaf": false, "exclusion_reason": "bogus"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].exclusion_reason is None


def test_parse_page_exclusion_reason_valid_and_invalid():
    valid = _parse_decision('{"links": [], "page_exclusion_reason": "sino_foreign_joint"}', [])
    assert valid.page_exclusion_reason == "sino_foreign_joint"
    invalid = _parse_decision('{"links": [], "page_exclusion_reason": "nope"}', [])
    assert invalid.page_exclusion_reason is None
    missing = _parse_decision('{"links": []}', [])
    assert missing.page_exclusion_reason is None


def test_parse_keeps_reslice_label_and_facet_axis():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "reslice", "confidence": 0.8, '
           '"is_leaf": false, "facet_axis": "title"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].label == "reslice"
    assert d.links[0].facet_axis == "title"


def test_parse_nulls_invalid_facet_axis():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "reslice", "confidence": 0.8, '
           '"is_leaf": false, "facet_axis": "bogus"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].facet_axis is None


def test_parse_ignores_facet_axis_when_label_not_reslice():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "detail", "confidence": 0.9, '
           '"is_leaf": true, "facet_axis": "title"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].facet_axis is None
