from dext.llm.client import LLMResponse
from dext.llm.extractor import ExtractionResult, _result_from_response
from dext.page.links import PageSnapshot

_RICH = "个人简介：张三，教授，博士生导师。教育经历……研究方向：人工智能。" * 8


def _snap(text, url="https://x.edu.cn/teacher/info/1001"):
    return PageSnapshot(url=url, final_url=url, title="t", text_snapshot=text,
                        links=[], link_signals=[], content_hash="h")


def test_valid_tool_call_yields_sanitized_payloads():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [
            {"name": "  张 三 ", "research_areas": ["AI", "AI"], "email": "bad"},
            {"title": "no name here"},
        ]},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert isinstance(out, ExtractionResult)
    assert out.failure_type is None
    assert len(out.payloads) == 1  # the no-name record was dropped by sanitize
    assert out.payloads[0].name == "张 三"
    assert out.payloads[0].research_areas == "AI"
    assert out.payloads[0].email is None


def test_invalid_tool_calls_classified_invalid_json():
    resp = LLMResponse(content=None, invalid_tool_calls=[
        {"name": "save_professors", "arguments_raw": "{bad", "error": "x"}
    ])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.failure_type == "invalid_json"
    assert out.payloads == []
    assert "{bad" in out.raw_preview


def test_no_records_rich_page_recoverable():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors", "arguments": {"professors": []}
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.failure_type == "no_structured_data"
    assert out.recoverable is True


def test_no_records_sparse_page_terminal():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors", "arguments": {"professors": []}
    }])
    out = _result_from_response(resp, _snap("欢迎光临"))
    assert out.failure_type == "no_structured_data"
    assert out.recoverable is False


def test_empty_with_valid_exclusion_reason_is_excluded():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [], "exclusion_reason": "administration"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.payloads == []
    assert out.failure_type == "excluded"
    assert out.exclusion_reason == "administration"


def test_payloads_with_position_exclusion_reason_are_excluded():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {
            "professors": [{"name": "韩建汶", "title": "专职辅导员", "email": "hjw@x.edu.cn"}],
            "exclusion_reason": "student_affairs",
        },
    }])
    out = _result_from_response(resp, _snap("韩建汶 专职辅导员 邮箱：hjw@x.edu.cn"))
    assert out.payloads == []
    assert out.failure_type == "excluded"
    assert out.exclusion_reason == "student_affairs"


def test_empty_student_affairs_detail_is_excluded():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [], "exclusion_reason": "student_affairs"},
    }])
    out = _result_from_response(resp, _snap("韩建汶 专职辅导员 邮箱：hjw@x.edu.cn"))
    assert out.payloads == []
    assert out.failure_type == "excluded"
    assert out.exclusion_reason == "student_affairs"


def test_payloads_win_over_non_position_exclusion_reason():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [{"name": "张三"}], "exclusion_reason": "arts"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert len(out.payloads) == 1
    assert out.failure_type is None
    assert out.exclusion_reason is None


def test_invalid_exclusion_reason_falls_through_to_no_data():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [], "exclusion_reason": "bogus"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.failure_type == "no_structured_data"
    assert out.exclusion_reason is None
