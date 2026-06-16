from dext.exclusions import (
    EXCLUSION_CATEGORIES,
    EXCLUSION_AXIS_ORG,
    EXCLUSION_AXIS_UNIT,
    ExclusionCategory,
    is_valid_exclusion_reason,
    render_exclusion_policy,
)


def test_vocabulary_has_14_categories_with_valid_axes():
    assert len(EXCLUSION_CATEGORIES) == 14
    codes = [c.code for c in EXCLUSION_CATEGORIES]
    assert len(set(codes)) == 14  # 无重复
    for c in EXCLUSION_CATEGORIES:
        assert isinstance(c, ExclusionCategory)
        assert c.axis in (EXCLUSION_AXIS_ORG, EXCLUSION_AXIS_UNIT)
        assert c.zh  # 非空中文概念
    a = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG)
    b = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT)
    assert (a, b) == (9, 5)


def test_is_valid_exclusion_reason():
    assert is_valid_exclusion_reason("sino_foreign_joint")
    assert is_valid_exclusion_reason("administration")
    assert not is_valid_exclusion_reason("bogus")
    assert not is_valid_exclusion_reason(None)
    assert not is_valid_exclusion_reason("")


def test_render_policy_contains_general_terms_not_scu():
    text = render_exclusion_policy()
    for term in ("中外合作办学", "联合办学", "艺术学院", "体育学院", "成人教育",
                 "继续教育", "基础教学中心", "实验中心", "博士后", "离退休",
                 "行政岗", "教辅岗", "人事工作", "筹建", "筹备",
                 "卓越工程师学院", "书院", "宁可漏排除", "国际关系学院",
                 "行政法", "行政管理"):
        assert term in text, term
    for term in ("SCUPI", "匹兹堡", "ltxjs", "bshldz", "吴玉章", "吴健雄", "scu.edu.cn"):
        assert term not in text, term
