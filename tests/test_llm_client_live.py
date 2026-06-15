from dext.llm.client import LLMResponse
from dext.llm.prompts import SAVE_PROFESSORS_TOOL


async def test_plain_chat_returns_content(llm_client):
    resp = await llm_client.chat(
        [{"role": "user", "content": "只回复一个词：OK"}],
        thinking=False,
    )
    assert isinstance(resp, LLMResponse)
    assert resp.content is not None and resp.content.strip() != ""


async def test_tool_call_auto_with_thinking_parses_arguments(llm_client):
    # DeepSeek V4: thinking mode rejects a FORCED tool_choice; "auto" is required.
    snapshot_text = "张三，教授，研究方向：人工智能。邮箱 zhangsan@x.edu.cn"
    resp = await llm_client.chat(
        [
            {"role": "system", "content": "通过 save_professors 工具返回正文中的教师。"},
            {"role": "user", "content": snapshot_text},
        ],
        tools=[SAVE_PROFESSORS_TOOL],
        tool_choice="auto",
        thinking=True,
    )
    assert not resp.invalid_tool_calls
    assert resp.tool_calls and resp.tool_calls[0]["name"] == "save_professors"
    assert "professors" in resp.tool_calls[0]["arguments"]
