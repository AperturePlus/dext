"""Explicit exclusion category vocabulary, shared by prompts and engine.

This module holds **no matching heuristics** — exclusion judgment is the LLM's
(decider + extractor). Here we only define the canonical category vocabulary,
render the policy text injected into the prompts, and validate the
`exclusion_reason` codes the model emits.
"""

from __future__ import annotations

from dataclasses import dataclass

EXCLUSION_AXIS_ORG = "A"   # 整个机构（学院/中心）
EXCLUSION_AXIS_UNIT = "B"  # 正常学院内部的人员/页面子类


@dataclass(frozen=True)
class ExclusionCategory:
    code: str
    zh: str
    axis: str


EXCLUSION_CATEGORIES: tuple[ExclusionCategory, ...] = (
    ExclusionCategory("sino_foreign_joint", "中外合作办学 / 联合办学（按办学性质，不因“国际”二字）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("arts", "艺术学院（艺术 / 美术 / 音乐 / 设计类）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("sports", "体育学院 / 体育部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("continuing_education", "成人教育 / 继续教育 / 网络教育学院", EXCLUSION_AXIS_ORG),
    ExclusionCategory("basic_education_center", "基础教学中心 / 基教中心 / 公共课教学部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("experiment_center", "实验中心 / 实验教学中心", EXCLUSION_AXIS_ORG),
    ExclusionCategory("under_construction", "筹建学院（学院（筹）/ 筹建 / 筹备）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("excellence_engineer", "卓越工程师学院（专项培养，通常无独立师资名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("academy", "书院（住宿制 / 通识，通常无独立教师名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("postdoc", "博士后流动站 / 博士后工作站 / 博士后人员栏目（普通教师履历不算）", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("retired", "离退休教师 / 离退休教职工", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("administration", "专职行政 / 行政岗 / 行政人员 / 行政团队 / 管理岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("support_role", "教辅岗 / 实验技术岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("personnel_work", "人事工作栏目（非教师）", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("party_building", "党建 / 党务 / 党委工作栏目", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("student_affairs", "学工 / 学生工作 / 辅导员队伍栏目", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("finance_admin", "财务 / 财务管理 / 报销收费等行政财务栏目（学科专业或研究方向不算）", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("lecture_event", "讲座 / 学术报告 / 论坛活动栏目（普通教师履历中的讲座经历不算）", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("visiting_professor", "客座教授 / 讲座教授 / 访问教授栏目", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("industry_mentor", "行业导师 / 企业导师 / 校外产业导师栏目", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("foreign_faculty", "外籍教师 / 外籍专家栏目", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("adjunct_mentor", "兼职导师 / 兼职教授 / 兼职研究生导师栏目", EXCLUSION_AXIS_UNIT),
)

_VALID_CODES: frozenset[str] = frozenset(c.code for c in EXCLUSION_CATEGORIES)


def is_valid_exclusion_reason(code: str | None) -> bool:
    """LLM 回的 exclusion_reason 是否落在词表内（解析时校验，非法归 None）。"""
    return bool(code) and code in _VALID_CODES


def render_exclusion_policy() -> str:
    """生成注入三份提示词的同一段排除策略文字；不含任何具体学校的专有名/域名/路径。"""
    axis_a = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG
    )
    axis_b = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT
    )
    return (
        "排除策略（宁可漏排除，不要错排除）：信号不明确时一律保留给后续爬取，不要因泛词误伤。\n"
        "排除判断由模型根据上下文保守完成；只有页面、栏目或链接身份明确属于排除类别时才排除。\n"
        "轴 A——当前**整个机构（学院/中心）**属下列类别时排除：\n"
        f"{axis_a}\n"
        "轴 B——正常学院内部的下列**人员/页面子类**排除（学院本身保留）：\n"
        f"{axis_b}\n"
        "反误伤：不因“国际 / 工程 / 工程师”等泛词排除正常招生学院（如国际关系学院）；"
        "不因“行政法 / 行政管理 / 财务管理”等学科、专业、研究方向或普通履历文字排除正常教师；"
        "不因普通教师履历中出现博士后经历、讲座经历、海外经历等文字排除正常教师。"
    )
