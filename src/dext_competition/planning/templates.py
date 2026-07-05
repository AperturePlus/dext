"""Deterministic preparation templates assembled from catalog facts."""
from __future__ import annotations

from dext_competition.contracts.catalog import CompetitionCard
from dext_competition.planning.schemas import PhaseTemplate, TemplateTask


_CATEGORY_TASKS = {
    "计算机": ("算法与代码基础训练", "完成代码仓库与复盘记录"),
    "电子信息": ("完成电路与器件基础训练", "搭建硬件联调环境"),
    "数学建模": ("完成建模方法与工具训练", "复现一套往届题流程"),
    "机器人": ("完成机械控制基础训练", "搭建仿真与联调环境"),
    "工学": ("完成工程规范与计算基础", "拆解工程约束和验收指标"),
    "经管": ("完成案例分析框架训练", "建立数据与论证材料库"),
    "综合与创业": ("明确用户问题与价值假设", "完成原型和验证记录"),
    "语言艺术": ("完成表达与文本基本功训练", "建立作品修改记录"),
    "医学生命科学": ("完成研究伦理与方法基础", "建立实验或数据分析记录"),
}


def build_phase_templates(
    card: CompetitionCard,
    *,
    time_model: str,
    experience_level: str,
) -> tuple[PhaseTemplate, ...]:
    foundations = _CATEGORY_TASKS.get(card.category, ("完成领域基础训练", "建立过程记录"))
    phases = [
        PhaseTemplate("foundation", "基础准备", 0.22, (
            TemplateTask("foundation-core", foundations[0], 12, True),
            TemplateTask("foundation-record", foundations[1], 6, experience_level == "beginner"),
        )),
        PhaseTemplate("practice", "专项实践", 0.34, (
            TemplateTask("practice-required", "完成至少一轮专项实践", 20, True),
            TemplateTask("practice-review", "进行阶段复盘并修正方案", 6, False),
            TemplateTask("practice-replication", "复现高质量案例或代码", 12, False, "experienced"),
        )),
        PhaseTemplate("simulation", "模拟与验收", 0.24, (
            TemplateTask("simulation-required", "完成全流程模拟", 12, True),
            TemplateTask("simulation-review", "按验收清单修复问题", 8, True),
        )),
    ]
    if time_model == "submission_deadline":
        phases.extend((
            PhaseTemplate("submission", "提交准备", 0.12, (
                TemplateTask("submission-package", "完成提交材料与合规检查", 8, True),
            )),
            PhaseTemplate("defense_prep", "答辩准备", 0.08, (
                TemplateTask("defense-required", "准备答辩材料并完成演练", 8, True),
                TemplateTask("defense-advanced", "进行压力问答和验收", 6, False, "experienced"),
            )),
        ))
    else:
        phases.extend((
            PhaseTemplate("event_sprint", "比赛窗口冲刺", 0.12, (
                TemplateTask("event-required", "执行比赛窗口分工与检查", 8, True),
            )),
            PhaseTemplate("recovery", "恢复与复盘", 0.08, (
                TemplateTask("recovery-required", "完成赛后归档和复盘", 4, True),
            )),
        ))
    return tuple(phases)


__all__ = ["build_phase_templates"]
