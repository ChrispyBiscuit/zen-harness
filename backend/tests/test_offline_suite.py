"""pytest entry for the offline regression suite (offline_suite.py): one pytest test per case.

No model, GPU or app server needed. Cases named probe_* document known risks and are reported as xfail."""
import pytest

import offline_suite as suite


@pytest.mark.parametrize("case", suite.TESTS, ids=lambda f: f.__name__)
def test_offline(case):
    if case.__name__.startswith("probe_"):
        try:
            case()
        except AssertionError as ex:
            pytest.xfail(str(ex) or "known risk")
        return
    case()
