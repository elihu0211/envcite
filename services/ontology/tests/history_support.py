"""歷史版本測試共用：整批驗收的比例在只有一兩部法規時沒有意義。"""

import pytest

from envcite_ontology.history_sync.policy import LIMITS


def relax_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """把整批驗收的每個上限放寬到 100%；個別測試要驗上限時再自己設。"""
    for key, (description, _) in list(LIMITS.items()):
        monkeypatch.setitem(LIMITS, key, (description, 1.0))
