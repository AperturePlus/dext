from dext.engine.workers import _payloads_with_system_homepage
from dext.types import ProfessorPayload


def test_system_homepage_fills_missing_payload_homepage():
    payloads = [ProfessorPayload(name="张三", external_link="https://scholar.google.com/citations?user=x")]

    out = _payloads_with_system_homepage(payloads, "https://x.edu.cn/teacher/zhang.htm")

    assert out[0].homepage == "https://x.edu.cn/teacher/zhang.htm"
    assert out[0].external_link == "https://scholar.google.com/citations?user=x"


def test_system_homepage_overrides_llm_payload_homepage():
    payloads = [
        ProfessorPayload(
            name="李四",
            homepage="https://wrong.example.com/li",
            external_link="https://li.example.com/",
        )
    ]

    out = _payloads_with_system_homepage(payloads, "https://x.edu.cn/teacher/li.htm")

    assert out[0].homepage == "https://x.edu.cn/teacher/li.htm"
    assert out[0].external_link == "https://li.example.com/"
    assert payloads[0].homepage == "https://wrong.example.com/li"
