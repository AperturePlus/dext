from __future__ import annotations

from aiohttp import web

from dext_recommend.api.middleware import ok


_MENTOR_PROMPTS = [
    {"text": "我想找计算机视觉方向的导师，最好在北京。"},
    {"text": "推荐医学影像和机器学习方向的导师。"},
    {"text": "我想申请硕士，帮我找匹配的导师。"},
]


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/home/prompts", handle_prompts),
        web.get(f"{prefix}/home/config", handle_config),
    ]


async def handle_prompts(request: web.Request) -> web.Response:
    return ok(_MENTOR_PROMPTS)


async def handle_config(request: web.Request) -> web.Response:
    return ok({
        "taglines": ["说说你想研究的方向，我帮你找到合适的导师"],
        "quick_tags": ["计算机视觉", "自然语言处理", "医学影像", "北京", "上海"],
        "prompts": _MENTOR_PROMPTS,
    })


__all__ = ["routes"]
