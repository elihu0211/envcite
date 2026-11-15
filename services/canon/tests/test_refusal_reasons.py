"""端到端評估對 consult 拒答原因的對應，必須與契約列舉（proto 產生碼）逐一相符。

契約新增拒答原因而評估工具沒跟上時，這裡失敗；不是等到跑評估才在中途停下。
"""

from envcite.consult.v2 import consult_pb2
from tools.answer_quality import (
    FINALS,
    KNOWN_FINALS,
    REFUSAL_FINAL,
    REFUSAL_UNEXPECTED,
)


def test_every_reason_in_the_contract_is_classified_or_declared_unexpected() -> None:
    contract = set(consult_pb2.Refused.Reason.keys())
    assert set(REFUSAL_FINAL) | REFUSAL_UNEXPECTED == contract
    assert not set(REFUSAL_FINAL) & REFUSAL_UNEXPECTED


def test_the_fourth_reason_is_a_floor_refusal_of_its_own_class() -> None:
    assert REFUSAL_FINAL["REASON_LOCAL_LAW_UNCOVERED"] == "refused_local_law_uncovered"
    assert len(set(REFUSAL_FINAL.values())) == len(REFUSAL_FINAL)


def test_every_class_a_reason_maps_to_is_known_and_listed_in_the_report() -> None:
    assert set(REFUSAL_FINAL.values()) <= KNOWN_FINALS
    assert set(FINALS) == KNOWN_FINALS
    assert len(FINALS) == len(KNOWN_FINALS)
