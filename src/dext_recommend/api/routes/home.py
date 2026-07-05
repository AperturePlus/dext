from __future__ import annotations

from aiohttp import web

from dext_recommend.api.middleware import ApiError, ok


_HOME_CONFIGS = {
    "mentor": {
        "taglines": ["说说你想研究的方向，我帮你找到合适的导师"],
        "quick_tags": ["计算机视觉", "自然语言处理", "医学影像", "北京", "上海"],
        "prompts": [
            {"text": "我想找计算机视觉方向的导师，最好在北京。"},
            {"text": "推荐医学影像和机器学习方向的导师。"},
            {"text": "我想申请硕士，帮我找匹配的导师。"},
        ],
    },
    "competition": {
        "taglines": ["说说你的专业、时间和目标，我帮你找到合适的竞赛"],
        "quick_tags": ["计算机", "数学建模", "机器人", "电子信息", "商科"],
        "prompts": [
            {"text": "我是计算机专业，想找适合大二参加的竞赛。"},
            {"text": "推荐适合零基础组队准备的数学建模竞赛。"},
            {"text": "我每周能投入 6 小时，想做能提升简历的比赛。"},
        ],
    },
}


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/home/prompts", handle_prompts),
        web.get(f"{prefix}/home/config", handle_config),
    ]


async def handle_prompts(request: web.Request) -> web.Response:
    return ok(_config_for(request)["prompts"])


async def handle_config(request: web.Request) -> web.Response:
    return ok(_config_for(request))


def _config_for(request: web.Request) -> dict:
    mode = (request.query.get("mode") or "mentor").strip() or "mentor"
    config = _HOME_CONFIGS.get(mode)
    if config is None:
        raise ApiError(422, "invalid_mode", "mode must be mentor or competition")
    return {
        "taglines": list(config["taglines"]),
        "quick_tags": list(config["quick_tags"]),
        "prompts": [dict(prompt) for prompt in config["prompts"]],
    }


__all__ = ["routes"]
