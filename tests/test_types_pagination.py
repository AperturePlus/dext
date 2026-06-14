from dataclasses import fields

from dext.types import FetchAction, PaginationState


def test_pagination_state_required_and_optional_fields():
    s = PaginationState(
        kind="form_submit",
        state_id="form:fromWen:fromWenNOWPAGE:2",
        label="fromWen 第 2 页",
        page_index=2,
        form_name="fromWen",
        fields={"fromWenNOWPAGE": "2"},
        submit=True,
        synthetic_url="https://x/xylb.jsp?__ycl_page=2",
        url="https://x/xylb.jsp",
    )
    assert s.kind == "form_submit"
    assert s.total_pages is None  # optional, defaults None
    assert s.fields == {"fromWenNOWPAGE": "2"}


def test_pagination_state_mirrors_types_ts_field_set():
    names = {f.name for f in fields(PaginationState)}
    assert names == {
        "kind", "state_id", "label", "page_index", "total_pages",
        "form_name", "fields", "submit", "synthetic_url", "url",
    }


def test_fetch_action_defaults_optional():
    a = FetchAction(kind="form_submit")
    assert a.form_name is None
    assert a.fields is None
    assert a.submit is None
    assert a.page_index is None


def test_dtos_exported_from_types_all():
    import dext.types as t

    assert "PaginationState" in t.__all__
    assert "FetchAction" in t.__all__
