from dext.exclusions import (
    EXCLUSION_CATEGORIES,
    EXCLUSION_AXIS_ORG,
    EXCLUSION_AXIS_UNIT,
    ExclusionCategory,
    is_valid_exclusion_reason,
    render_exclusion_policy,
)


def test_vocabulary_has_expected_categories_with_valid_axes():
    assert len(EXCLUSION_CATEGORIES) == 24
    codes = [c.code for c in EXCLUSION_CATEGORIES]
    assert len(set(codes)) == 24  # 无重复
    for c in EXCLUSION_CATEGORIES:
        assert isinstance(c, ExclusionCategory)
        assert c.axis in (EXCLUSION_AXIS_ORG, EXCLUSION_AXIS_UNIT)
        assert c.zh  # 非空中文概念
    a = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG)
    b = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT)
    assert (a, b) == (11, 13)


def test_is_valid_exclusion_reason():
    assert is_valid_exclusion_reason("sino_foreign_joint")
    assert is_valid_exclusion_reason("administration")
    assert is_valid_exclusion_reason("party_building")
    assert is_valid_exclusion_reason("adjunct_mentor")
    assert not is_valid_exclusion_reason("bogus")
    assert not is_valid_exclusion_reason(None)
    assert not is_valid_exclusion_reason("")


def test_render_policy_contains_general_terms_not_scu():
    text = render_exclusion_policy()
    for term in ("中外合作办学", "联合办学", "艺术学院", "体育学院", "成人教育",
                 "继续教育", "基础教学中心", "实验中心", "博士后", "离退休",
                 "行政岗", "教辅岗", "人事工作", "筹建", "筹备",
                 "卓越工程师学院", "书院", "人名命名", "新生学院", "本科生院",
                 "党建", "学工", "财务", "讲座",
                 "客座教授", "行业导师", "外籍教师", "兼职导师",
                 "宁可漏排除", "国际关系学院", "行政法", "行政管理",
                 "财务管理", "讲座经历", "海外经历"):
        assert term in text, term
    for term in ("SCUPI", "匹兹堡", "ltxjs", "bshldz", "吴玉章", "吴健雄", "scu.edu.cn"):
        assert term not in text, term
