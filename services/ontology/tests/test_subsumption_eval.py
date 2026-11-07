"""陳述進入推論後的涵攝評估單元測試。

驗證真實條文規範陳述（水污染防治法第 7 條與第 40 條）在三值涵攝、缺事實識別、
需判斷事項、按次處罰觸發與行政罰法總則阻卻各案例下的評估指標。
"""

import json
from datetime import date
from pathlib import Path

import pytest

from envcite_ontology.annotate import Loaded
from envcite_ontology.reasoning import (
    SubsumptionCase,
    evaluate_subsumption,
)
from tests.reasoning_support import (
    WATER,
    Conn,
)
from tests.verification_support import _with_penalties

BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "eval" / "subsumption_benchmark.json"


@pytest.fixture
def water_loaded(conn: Conn, sample: None) -> Loaded:
    loaded = _with_penalties(conn, json.loads(WATER.read_text(encoding="utf-8")))
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE assertion_kind = 'statement'"
    )
    conn.commit()
    return loaded


def _load_cases() -> list[SubsumptionCase]:
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    cases: list[SubsumptionCase] = []
    for item in data:
        as_of = date.fromisoformat(item["as_of"])
        decided_on = date.fromisoformat(item["decided_on"]) if item.get("decided_on") else None
        cases.append(
            SubsumptionCase(
                case_id=item["case_id"],
                title=item["title"],
                description=item.get("description", ""),
                as_of=as_of,
                decided_on=decided_on,
                case_facts=item["case_facts"],
                expected_verdicts=item["expected_verdicts"],
                expected_penalties=tuple(
                    (p[0], p[1], bool(p[2])) for p in item.get("expected_penalties", [])
                ),
                expected_missing=tuple((m[0], m[1]) for m in item.get("expected_missing", [])),
                expected_judgements=tuple(
                    (j[0], j[1]) for j in item.get("expected_judgements", [])
                ),
                expected_unpunished=tuple(item.get("expected_unpunished", [])),
                expected_time_barred=tuple(item.get("expected_time_barred", [])),
            )
        )
    return cases


def test_subsumption_benchmark_all_cases_pass(conn: Conn, water_loaded: Loaded) -> None:
    cases = _load_cases()
    assert len(cases) == 10

    report = evaluate_subsumption(
        conn,
        cases,
        id_resolver=water_loaded.ids,
    )
    print("\n" + report.to_markdown())

    # 驗證整體通過率與指標皆達 100%
    assert report.total_cases == 10
    assert report.passed_cases == 10
    assert report.case_pass_rate == 1.0
    assert report.verdict_accuracy == 1.0
    assert report.missing_precision == 1.0
    assert report.missing_recall == 1.0
    assert report.judgement_precision == 1.0
    assert report.judgement_recall == 1.0
    assert report.penalty_precision == 1.0
    assert report.penalty_recall == 1.0

    # 驗證報告輸出格式
    md = report.to_markdown()
    assert "# 陳述進入推論後的涵攝評估報告" in md
    assert "CASE-01" in md
    assert "CASE-10" in md

    data = report.to_dict()
    assert data["total_cases"] == 10
    assert len(data["results"]) == 10


def test_subsumption_evaluator_catches_verdict_mismatch(conn: Conn, water_loaded: Loaded) -> None:
    # 故意構造一個錯誤預期的案例，驗證評估器能精確回報差異
    flawed_case = SubsumptionCase(
        case_id="FLAWED-01",
        title="預期錯誤測試",
        as_of=date(2019, 1, 1),
        case_facts={
            "actor_is_regulated_entity": 1,
            "discharges_to_surface_water": 1,
            "actor_is_business_or_sewer_system": 1,
            "actor_is_livestock_business": 0,
            "effluent_meets_standard": 0,  # 實際應為 violation
            "circumstances_serious": 0,
            "revocation_necessary": 0,
            "improvement_overdue": 0,
            "ap_intent": 1,
        },
        expected_verdicts={
            "w7_1": "compliant",  # 故意寫錯預期
        },
    )

    report = evaluate_subsumption(
        conn,
        [flawed_case],
        id_resolver=water_loaded.ids,
    )

    assert report.total_cases == 1
    assert report.passed_cases == 0
    assert report.case_pass_rate == 0.0
    assert report.verdict_accuracy == 0.0
    res = report.results[0]
    assert not res.passed
    assert not res.verdict_matches
    assert any("判決不符 [w7_1]" in d for d in res.diffs)
