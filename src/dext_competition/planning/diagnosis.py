"""Deterministic preparation-level diagnosis."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from dext_competition.contracts.plan import LevelDiagnosis


def diagnose_preparation_level(
    answers: Sequence[Mapping[str, object]],
    *,
    profile: Mapping[str, object] | None = None,
) -> LevelDiagnosis:
    text = " ".join(str(answer.get("answer", "")) for answer in answers).casefold()
    score = 0
    advanced_terms = ("获奖", "复现", "论文", "项目", "多次", "熟练", "advanced")
    intermediate_terms = ("参加过", "基础", "课程", "一次", "intermediate")
    score += sum(term in text for term in advanced_terms) * 2
    score += sum(term in text for term in intermediate_terms)
    if profile:
        score += 2 if profile.get("competition_experience") else 0
        score += 2 if profile.get("project_experience") else 0
    if score >= 6:
        return LevelDiagnosis("experienced", "已有较完整的项目或竞赛经验。", "增加模拟、复现、答辩和验收任务。")
    if score >= 2:
        return LevelDiagnosis("intermediate", "具备部分相关基础，但全流程经验有限。", "保留基础检查并加强专项实践。")
    return LevelDiagnosis("beginner", "当前信息未显示稳定的竞赛全流程经验。", "先补基础，再完成一轮小规模实践。")


__all__ = ["diagnose_preparation_level"]
