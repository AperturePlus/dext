from dext.page.pagination import extract_form_pagination_states, merge_pagination_states
from dext.types import PaginationState

# WebPlus-style form pagination: javascript anchors set a form field then submit.
_FORM_HTML = """
<html><body>
  <div class="pages">
    <span class="this-page">1</span>
    <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='2';document.forms['fromWen'].submit();">2</a>
    <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='3';document.forms['fromWen'].submit();">3</a>
  </div>
</body></html>
"""
_CURRENT_URL = "https://example.edu.cn/xylb.jsp?py=a"


def test_form_states_parsed_skipping_page_one_and_current():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    assert [s.page_index for s in states] == [2, 3]  # page 1 = current, skipped
    assert all(isinstance(s, PaginationState) for s in states)


def test_synthetic_url_is_byte_exact():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    page2 = next(s for s in states if s.page_index == 2)
    # MUST equal formPagination.ts buildSyntheticUrl(...) byte-for-byte.
    assert page2.synthetic_url == (
        "https://example.edu.cn/xylb.jsp?py=a"
        "&__ycl_kind=form&__ycl_form=fromWen&__ycl_field=fromWenNOWPAGE&__ycl_page=2"
    )


def test_state_id_label_fields_and_total_pages():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    page3 = next(s for s in states if s.page_index == 3)
    assert page3.state_id == "form:fromWen:fromWenNOWPAGE:3"
    assert page3.label == "fromWen 第 3 页"
    assert page3.fields == {"fromWenNOWPAGE": "3"}
    assert page3.submit is True
    assert page3.form_name == "fromWen"
    assert page3.url == _CURRENT_URL
    assert page3.total_pages == 3


def test_gopage_expands_to_one_through_max():
    html = """
    <html><body>
      <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='5';document.forms['fromWen'].submit();">5</a>
      <input name="fromWenGOPAGE" />
    </body></html>
    """
    states = extract_form_pagination_states(html, "https://example.edu.cn/xylb.jsp")
    # GOPAGE present → expand 1..5, skip page 1 (no .this-page, no page param → current=1).
    assert [s.page_index for s in states] == [2, 3, 4, 5]
    assert states[0].total_pages == 5


def test_existing_ycl_params_are_replaced_not_duplicated():
    url = "https://example.edu.cn/xylb.jsp?__ycl_page=9&py=a"
    html = (
        "<a href=\"javascript:document.forms['f'].FIELD.value='2';"
        "document.forms['f'].submit();\">2</a>"
    )
    states = extract_form_pagination_states(html, url)
    syn = states[0].synthetic_url
    assert syn.count("__ycl_page=") == 1
    assert syn.endswith("__ycl_page=2")
    assert "py=a" in syn


def test_no_form_anchors_returns_empty():
    assert extract_form_pagination_states("<a href='/szdw/2.htm'>2</a>", _CURRENT_URL) == []


def test_merge_dedups_by_synthetic_url_reported_wins():
    parsed = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    reported = [
        PaginationState(
            kind="form_submit", state_id="form:fromWen:fromWenNOWPAGE:2",
            label="reported", page_index=2, form_name="fromWen",
            fields={"fromWenNOWPAGE": "2"}, submit=True,
            synthetic_url=parsed[0].synthetic_url, url=_CURRENT_URL, total_pages=3,
        )
    ]
    merged = merge_pagination_states(reported, parsed)
    page2 = [s for s in merged if s.page_index == 2]
    assert len(page2) == 1                 # deduped by synthetic_url
    assert page2[0].label == "reported"    # reported wins
    assert {s.page_index for s in merged} == {2, 3}
