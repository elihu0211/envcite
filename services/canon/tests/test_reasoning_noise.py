"""依場所類別分級：噪音管制法第 9、24 條（`tests/sample.py` 的 `noise_law()`），模型草稿，這裡確認。

第 24 條第 1 項同一個違反（經限期改善仍未符合噪音管制標準），依款別分五級罰鍰，各級上下限不同。
這組測試確認：每一級是自己的罰則陳述，錨在自己的款；案件事實給定類別時，只列該級；
類別不明時，各級都列出並附條件，系統不自行擇一。專論見 envcite-docs 01-08 §6.3。
"""

import json
from datetime import date
from pathlib import Path

import pytest

from canon.annotate import Loaded
from canon.law import importer
from canon.law.moj import Moj
from canon.reasoning.engine import Result, evaluate
from tests.reasoning_support import Conn
from tests.sample import noise_law, stored_snapshot
from tests.test_import import _scalar
from tests.verification_support import _with_penalties

DATA = Path(__file__).parent / "data" / "noise_art9_art24.json"
ON = date(2024, 1, 1)  # 2023-06-21 修正公布，自公布日施行

# 每一款的類別概念與其法定罰鍰上下限（條文：第 24 條第 1 項各款）。
TIERS = {
    "n24_factory_fine": ("site_is_factory", 6000, 60000),
    "n24_entertainment_business_fine": ("site_is_entertainment_or_business", 3000, 30000),
    "n24_construction_fine": ("site_is_construction", 18000, 180000),
    "n24_amplifier_fine": ("site_is_amplifier", 3000, 30000),
    "n24_announced_other_fine": ("site_is_announced_other", 3000, 30000),
}
SITES = [concept for concept, _, _ in TIERS.values()]

BASE: dict[str, float] = {
    "in_noise_control_zone": 1,
    "noise_within_standard": 0,
    "still_over_after_improvement_deadline": 1,
    "ap_intent": 1,  # 總則的有責性；這組案例測的是各法自己的罰則要件
}


@pytest.fixture
def noise(conn: Conn, tmp_path: Path) -> Loaded:
    stored_snapshot(conn, tmp_path, [noise_law()])
    importer.run(conn, tmp_path, Moj())
    loaded = _with_penalties(conn, json.loads(DATA.read_text(encoding="utf-8")))
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE assertion_kind = 'statement'"
    )
    conn.commit()
    return loaded


def _case(site: str | None, **more: float) -> dict[str, float]:
    """某一類別成立、其餘類別不成立的案件事實；`None` 表示類別沒給。"""
    case = dict(BASE) | more
    if site is not None:
        case |= {concept: float(concept == site) for concept in SITES}
    return case


def _tier_fines(result: Result, noise: Loaded) -> dict[str, tuple[int | None, int | None, bool]]:
    labels = {noise.ids[label]: label for label in TIERS}
    return {
        labels[p.statement_id]: (p.min_amount, p.max_amount, p.conditional)
        for p in result.penalties
        if p.statement_id in labels
    }


def test_every_tier_is_its_own_penalty_anchored_to_its_own_item(conn: Conn, noise: Loaded) -> None:
    anchors: dict[object, object] = dict(
        conn.execute(
            "SELECT sn.min_amount, count(*) FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS st ON st.id = sn.statement_id"
            "  JOIN corpus.provision AS p ON p.id = st.provision_id"
            " WHERE sn.legal_effect = 'fine' AND NOT sn.per_occasion"
            "   AND p.path ~ '^art_24/para_1/pt_[1-5]$' GROUP BY 1"
        ).fetchall()  # type: ignore[arg-type]
    )

    assert anchors == {3000: 3, 6000: 1, 18000: 1}  # 五款，三款同額；各錨在自己的款
    paths = {
        str(path)
        for (path,) in conn.execute(
            "SELECT p.path FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS st ON st.id = sn.statement_id"
            "  JOIN corpus.provision AS p ON p.id = st.provision_id"
            " WHERE sn.legal_effect = 'fine' AND NOT sn.per_occasion AND sn.min_amount IS NOT NULL"
        )
    }
    assert paths == {f"art_24/para_1/pt_{n}" for n in range(1, 6)}


def test_a_known_site_gets_only_its_own_tier(conn: Conn, noise: Loaded) -> None:
    for label, (concept, lo, hi) in TIERS.items():
        result = evaluate(conn, ON, _case(concept))

        assert _tier_fines(result, noise) == {label: (lo, hi, False)}, label


def test_an_unknown_site_lists_every_tier_as_conditional_and_picks_none(
    conn: Conn, noise: Loaded
) -> None:
    result = evaluate(conn, ON, _case(None))

    assert _tier_fines(result, noise) == {
        label: (lo, hi, True) for label, (_, lo, hi) in TIERS.items()
    }


def test_the_ladder_follows_the_failed_improvement_not_the_site(conn: Conn, noise: Loaded) -> None:
    """階梯的各階（命令之外）都以「經限期改善仍未符合」為前提：未屆期則一階都不列。"""
    result = evaluate(conn, ON, _case("site_is_factory", still_over_after_improvement_deadline=0))

    assert result.penalties == ()


def test_the_ladder_above_the_fine_is_discretionary_and_the_revocation_needs_judgement(
    conn: Conn, noise: Loaded
) -> None:
    case = _case(
        "site_is_construction", holds_permit=1, revocation_necessary=1, operator_is_legal_person=0
    )

    result = evaluate(conn, ON, case)

    effects = {
        p.statement_id: p.effect
        for p in result.penalties
        if p.statement_id not in {noise.ids[label] for label in TIERS}
    }
    assert effects == {
        noise.ids["n24_repeat"]: "fine",
        noise.ids["n24_suspend"]: "restriction",
        noise.ids["n24_revoke"]: "deprivation",
    }
    # 「必要時」是不確定法律概念：系統不判定，呼叫端給的值視為其判斷。
    assert (noise.ids["n24_revoke"], "revocation_necessary") in result.judgement_used
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.sanction WHERE discretionary",
        )
        == 3
    )


def test_the_legal_persons_representative_is_a_separate_liable_party(
    conn: Conn, noise: Loaded
) -> None:
    """負責人另受處罰（第 24 條第 3 項）：責任基礎是代表人，只在行為人屬法人時列出。"""
    legal = evaluate(conn, ON, _case("site_is_factory", operator_is_legal_person=1))
    natural = evaluate(conn, ON, _case("site_is_factory", operator_is_legal_person=0))

    rep = noise.ids["n24_representative"]
    assert rep in {p.statement_id for p in legal.penalties}
    assert rep not in {p.statement_id for p in natural.penalties}
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.statement_slot"
            " WHERE liability_basis = 'representative'",
        )
        == 1
    )
