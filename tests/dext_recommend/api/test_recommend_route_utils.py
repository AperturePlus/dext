from __future__ import annotations

from types import SimpleNamespace

import pytest

from dext_recommend.api.middleware import ApiError
from dext_recommend.api.routes._utils import raise_if_domain_error


@pytest.mark.parametrize("code", ["generation_parse_error", "schema_validation_failed"])
def test_raise_if_domain_error_maps_generation_parse_failures_to_503(code):
    with pytest.raises(ApiError) as excinfo:
        raise_if_domain_error([
            SimpleNamespace(code=code, message="generation failed", severity="error")
        ])

    assert excinfo.value.status == 503
    assert excinfo.value.error_code == code
    assert excinfo.value.message == "generation failed"
