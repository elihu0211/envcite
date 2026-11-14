"""整合測試：行政罰法三階審查、跨法概念、度量衡正規化、多重法規命令限值與裁罰矩陣計算（01-08）。"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from envcite_ontology.annotate import load
from envcite_ontology.db import Conn
from envcite_ontology.reasoning import evaluate
from envcite_ontology.reasoning.facts.case_inputs import PerOccasionTimeline
from envcite_ontology.reasoning.scoring.adjustments import compute_lecture_hours
from envcite_ontology.reasoning.scoring.audit import (
    DispositionAuditResult,
    DispositionCitation,
    DispositionElement,
    audit_disposition,
)
from envcite_ontology.reasoning.scoring.evaluate import evaluate_matrix
from envcite_ontology.reasoning.scoring.matrices import load_matrices
from envcite_ontology.reasoning.scoring.models import FactorSpec, PenaltyMatrixSpec, ScoringInput
from envcite_ontology.reasoning.units import canonical_unit_name, normalize_value
from envcite_ontology.reasoning_codec import disposition as disposition_codec
from envcite_ontology.reasoning_codec import outcome as outcome_codec
from envcite_ontology.reasoning_codec import request as request_codec
from tests.reasoning_support import _scope
from tests.test_annotation import BEFORE, LAW, _doc, _duty, _limit
from tests.test_incremental import _first, _law_v1


def _stmt(ident: str, path: str, text: str, **body: Any) -> dict[str, Any]:
    return {"id": ident, "law": LAW, "path": path, "text": text, **body}


def _water_and_air_doc(*statements: dict[str, Any]) -> dict[str, Any]:
    doc = _doc(*statements)
    doc["concepts"] += [
        {"code": "ap_intent", "label": "故意"},
        {"code": "ap_negligence", "label": "過失"},
        {"code": "ap_no_intent_or_negligence", "label": "無故意過失"},
        {"code": "ap_fact_mistake", "label": "構成要件錯誤"},
        {"code": "ap_lawful_duty", "label": "依法令之行為"},
        {"code": "ap_self_defense", "label": "正當防衛"},
        {"code": "ap_necessity", "label": "緊急避難"},
        {"code": "ap_defense_excess", "label": "防衛或避難過當"},
        {"code": "ap_under_14", "label": "未滿十四歲"},
        {"code": "ap_14_to_18", "label": "十四歲以上未滿十八歲"},
        {"code": "ap_mental_incapacity", "label": "心神喪失或精神障礙致無辨識能力"},
        {"code": "ap_reduced_capacity", "label": "顯著減低辨識能力"},
        {"code": "ap_self_induced", "label": "原因自由行為"},
        {"code": "ap_ignorance_unavoidable", "label": "不知法規有正當理由且無法避免"},
        {"code": "ap_ignorance_avoidable", "label": "不知法規可避免"},
        {"code": "ap_petty_minor", "label": "情節輕微以不處罰為適當"},
        {"code": "ap_self_confess", "label": "自首或坦承違規"},
    ]
    return doc


def _penalty_stmt(
    stmt_id: str,
    punishes: str,
    effect: str,
    *,
    floor: int | None = None,
    cap: int | None = None,
    repeating: bool = False,
) -> dict[str, Any]:
    sanction: dict[str, Any] = {"punishes": punishes, "effect": effect, "repeating": repeating}
    if floor is not None:
        sanction["min"] = floor
    if cap is not None:
        sanction["max"] = cap
    return _stmt(
        stmt_id,
        "art_1",
        "甲一",
        kind="sanction",
        sanction=sanction,
    )


# ── 1. 度量衡正規化與異質單位檢查 ──────────────────────────────────────────


def test_units_normalization_and_mismatch_detection() -> None:
    # mg/L 與 ug/L 換算：500 ug/L == 0.5 mg/L
    val, ok = normalize_value(Decimal("500"), "ug/L", "mg/L")
    assert ok is True
    assert val == Decimal("0.5")
    assert canonical_unit_name("μg/L") == "μg/L"

    # mg/Nm3 vs ppm 為不同維度，應報 mismatch (None, False)
    bad_val, bad_ok = normalize_value(Decimal("120"), "mg/Nm3", "ppm")
    assert bad_ok is False
    assert bad_val is None


def test_reasoning_normalizes_fact_unit_to_limit_unit(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    doc = _water_and_air_doc(
        _duty(),
        _limit("乙一", 100),
        _penalty_stmt("pen_fine", "duty", "fine", floor=60000, cap=6000000),
    )
    ids = load(conn, doc).ids
    result = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150000.0, "ap_negligence": 1.0},
        fact_units={"cod": "ug/L"},
    )
    assert any(p.statement_id == ids["pen_fine"] for p in result.penalties)


# ── 2. 多重法規命令限值適用範圍（annotation.threshold_sources_at）──────────


def test_multi_scope_delegated_limits_select_by_scope_and_surface_ambiguity(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    lim100 = _limit("乙一", 100)
    lim100["conditions"].append(
        {
            "role": "scope",
            "clause": 1,
            "concept": "general_industry",
            "comparator": "eq",
            "threshold": 1,
        }
    )
    lim60 = _limit("乙一", 60)
    lim60["conditions"].append(
        {
            "role": "scope",
            "clause": 1,
            "concept": "electroplating",
            "comparator": "eq",
            "threshold": 1,
        }
    )
    doc = _water_and_air_doc(_duty(), lim100, lim60)
    doc["concepts"] += [
        {"code": "general_industry", "label": "一般事業"},
        {"code": "electroplating", "label": "電鍍業"},
    ]
    loaded = load(conn, doc)

    # Case A: 電鍍業應選 60，80 超標
    res_electro = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 80.0, "electroplating": 1.0, "general_industry": 0.0},
    )
    assert loaded.ids["duty"] in res_electro.verdicts

    # Case B: 一般事業應選 100，80 不超標 -> compliant
    res_gen = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 80.0, "general_industry": 1.0, "electroplating": 0.0},
    )
    assert res_gen.verdicts[loaded.ids["duty"]] == "compliant"

    # Case C: 未提供業別區辨事實時，應將其列為 missing 而不亂猜 min()
    res_missing = evaluate(conn, BEFORE, {"discharging": 1.0, "cod": 80.0})
    assert any(
        concept in ("general_industry", "electroplating") for _, concept in res_missing.missing
    )


# ── 3. 跨法概念階層（vocab.concept_relation broader / exact_match）─────────


def test_cross_law_concept_broader_infers_parent_concept(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    doc = _water_and_air_doc(
        _stmt(
            "duty_voc",
            "art_1",
            "甲一",
            kind="prescriptive",
            deontic="prohibition",
            conditions=[_scope("voc_emission")],
        )
    )
    doc["concepts"] += [
        {"code": "voc_emission", "label": "揮發性有機物排放"},
        {"code": "toluene_emission", "label": "甲苯排放"},
    ]
    loaded = load(conn, doc)
    child_row = conn.execute(
        "SELECT id FROM vocab.concept WHERE code = 'toluene_emission'"
    ).fetchone()
    parent_row = conn.execute("SELECT id FROM vocab.concept WHERE code = 'voc_emission'").fetchone()
    assert child_row is not None and parent_row is not None
    conn.execute(
        """
        INSERT INTO vocab.concept_relation (source_concept_id, target_concept_id, relation_kind)
        VALUES (%s, %s, 'broader')
        """,
        (child_row[0], parent_row[0]),
    )
    conn.commit()

    res = evaluate(conn, BEFORE, {"toluene_emission": 1.0})
    assert loaded.ids["duty_voc"] in res.verdicts


# ── 4. 行政罰法三階審查、原因自由行為、釋字 604 與多行為群組 ───────────────


def test_three_tier_jurisprudence_justified_excused_and_self_induced(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    doc = _water_and_air_doc(
        _duty(),
        _limit("乙一", 100),
        _penalty_stmt("pen_fine", "duty", "fine", floor=60000, cap=6000000),
    )
    doc["concepts"] += [
        {"code": "ap_defense_excessive", "label": "防衛過當"},
        {"code": "ap_capacity_reduced", "label": "辨識能力顯著減低"},
    ]
    ids = load(conn, doc).ids
    pen_id = ids["pen_fine"]

    # 4a. 正當防衛（第二階阻卻違法）-> justified 成立，罰鍰不列為可裁處
    res_justified = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0, "ap_negligence": 1.0, "ap_self_defense": 1.0},
    )
    assert (pen_id, "self_defense") in res_justified.general.justified
    assert not any(p.statement_id == pen_id for p in res_justified.penalties)

    # 4b. 防衛過當（§12 但書）：不阻卻違法，轉為「得減輕或免除」
    res_excess = evaluate(
        conn,
        BEFORE,
        {
            "discharging": 1.0,
            "cod": 150.0,
            "ap_negligence": 1.0,
            "ap_self_defense": 1.0,
            "ap_defense_excessive": 1.0,
        },
    )
    assert (pen_id, "self_defense") not in res_excess.general.justified
    assert any(p.statement_id == pen_id for p in res_excess.penalties)
    assert (pen_id, "excessive_defense") in res_excess.general.mitigation

    # 4c. 構成要件錯誤（第一階阻卻故意）
    res_mistake = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0, "ap_intent": 1.0, "ap_fact_mistake": 1.0},
    )
    assert not any(p.statement_id == pen_id for p in res_mistake.penalties)

    # 4d. §9 V 原因自由行為（限制責任能力＋自行招致）不適用減輕
    res_self_induced = evaluate(
        conn,
        BEFORE,
        {
            "discharging": 1.0,
            "cod": 150.0,
            "ap_negligence": 1.0,
            "ap_capacity_reduced": 1.0,
            "ap_self_induced": 1.0,
        },
    )
    assert (pen_id, "reduced_capacity") not in res_self_induced.general.mitigation

    # 4e. §8 不可避免不知法規（第三階阻卻責任 -> excused）
    res_ignorance = evaluate(
        conn,
        BEFORE,
        {
            "discharging": 1.0,
            "cod": 150.0,
            "ap_negligence": 1.0,
            "ap_ignorance_unavoidable": 1.0,
        },
    )
    assert (pen_id, "unavoidable_ignorance") in res_ignorance.general.excused
    assert not any(p.statement_id == pen_id for p in res_ignorance.penalties)


def test_per_occasion_grace_period_and_multi_act_groups(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    duty_2 = _stmt(
        "duty_2",
        "art_2",
        "乙一",
        kind="prescriptive",
        deontic="prohibition",
        conditions=[_scope("discharging")],
    )
    order = _penalty_stmt("order", "duty", "order")
    pen_1_stmt = _penalty_stmt("pen_1", "duty", "fine", floor=60000, cap=600000, repeating=True)
    pen_1_stmt["sanction"]["triggers"] = [{"statement": "order", "kind": "order_lapsed"}]
    doc = _water_and_air_doc(
        _duty(),
        duty_2,
        _limit("乙一", 100),
        order,
        pen_1_stmt,
        _penalty_stmt("pen_1_single", "duty", "fine", floor=60000, cap=600000),
        _penalty_stmt("pen_2", "duty_2", "fine", floor=100000, cap=1000000),
    )
    ids = load(conn, doc).ids
    pen_1 = ids["pen_1"]
    duty_1_id = ids["duty"]
    duty_2_id = ids["duty_2"]

    # 釋字 604：改善期限內（reinspection <= extension_deadline）不生按次處罰
    res_grace = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0, "ap_negligence": 1.0},
        timeline=PerOccasionTimeline(
            cure_notice_served_on=date(2025, 1, 1),
            cure_deadline=date(2025, 1, 15),
            extension_deadline=date(2025, 1, 31),
            reinspection_on=date(2025, 1, 25),
        ),
    )
    assert not any(p.statement_id == pen_1 for p in res_grace.penalties)

    # 分屬不同行為群組 act_a / act_b 時，應各自從一重計算，互不覆蓋
    res_multi = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0, "ap_negligence": 1.0},
        act_groups={"act_a": (duty_1_id,), "act_b": (duty_2_id,)},
    )
    assert res_multi.one_act_groups == {
        "act_a": (60000, 600000),
        "act_b": (100000, 1000000),
    }


# ── 5. 裁罰矩陣計算引擎、環境講習時數與處分書要件檢核（CQ D1–D3）──────────


def test_scoring_models_water_air_waste_construction_and_lecture() -> None:
    # 5a. 水污法點數×基數制：點數 (4+2-1)=5 × 基數 6000 = 30,000（clamp 至最低 60,000）
    water_spec = PenaltyMatrixSpec(
        matrix_code="water_effluent",
        law_title="水污染防治法罰鍰額度裁罰準則",
        norm_kind="delegated_regulation",
        formula_kind="additive_points_times_base",
        legal_effect="fine",
        base_amount=Decimal("6000"),
        statutory_min=Decimal("60000"),
        statutory_max=Decimal("20000000"),
        factors=(
            FactorSpec("violation_type", "base_point", "違規態樣點數", "附表一"),
            FactorSpec("excess_ratio", "aggravation_point", "超標倍數加重點數", "附表一"),
            FactorSpec("first_offense_mitigation", "mitigation_point", "減輕點數", "§4"),
        ),
    )
    water_res = evaluate_matrix(
        water_spec,
        ScoringInput(
            "water_effluent",
            {
                "violation_type": Decimal("4"),
                "excess_ratio": Decimal("2"),
                "first_offense_mitigation": Decimal("1"),
            },
        ),
    )
    assert water_res.raw_min == Decimal("30000")
    assert water_res.clamped_min == 60000
    assert water_res.clamped_max == 60000

    # 5b. 空污法權重相乘制：下限 100,000 × A(2) × B(1.5) × C(1) × D(1) × (1+0.2) = 360,000
    air_spec = PenaltyMatrixSpec(
        matrix_code="air_stationary",
        law_title="固定污染源空污裁罰準則",
        norm_kind="delegated_regulation",
        formula_kind="multiplicative_factors",
        legal_effect="fine",
        base_amount=Decimal("100000"),
        statutory_min=Decimal("100000"),
        statutory_max=Decimal("20000000"),
        factors=(
            FactorSpec("A", "multiplicative_factor", "污染程度", "§3 附表一"),
            FactorSpec("B", "multiplicative_factor", "污染物項目", "§3 附表一"),
            FactorSpec("C", "multiplicative_factor", "污染特性", "§3 附表一"),
            FactorSpec("D", "multiplicative_factor", "影響程度", "§3 附表一"),
            FactorSpec("E", "additive_rate", "加重或減輕事項", "§3", needs_judgement=True),
        ),
    )
    air_res = evaluate_matrix(
        air_spec,
        ScoringInput(
            "air_stationary",
            {
                "A": Decimal("2"),
                "B": Decimal("1.5"),
                "C": Decimal("1"),
                "D": Decimal("1"),
                "E": Decimal("0.2"),
            },
        ),
    )
    assert air_res.clamped_min == 360000
    assert air_res.clamped_max == 360000

    # 未填 E 時保留區間與 judgement_factors，絕不猜值
    air_open = evaluate_matrix(
        air_spec,
        ScoringInput(
            "air_stationary",
            {"A": Decimal("2"), "B": Decimal("1.5"), "C": Decimal("1"), "D": Decimal("1")},
        ),
    )
    assert "E" in air_open.judgement_factors
    assert air_open.clamped_min is None

    # 5c. 營建工程缺失記點：>= 10 點觸發違規
    const_spec = PenaltyMatrixSpec(
        matrix_code="construction_demerit",
        law_title="營建工程空氣污染防制設施缺失記點及處理原則",
        norm_kind="discretion_standard",
        formula_kind="demerit_accumulation",
        legal_effect="fine",
        base_amount=Decimal("20000"),
        statutory_min=Decimal("20000"),
        statutory_max=Decimal("100000"),
        demerit_threshold=Decimal("10"),
        factors=(FactorSpec("fence_missing", "demerit_point", "圍籬缺失", "原則§4"),),
    )
    const_under = evaluate_matrix(
        const_spec, ScoringInput("construction_demerit", {"fence_missing": Decimal("8")})
    )
    assert const_under.demerit_triggered is False
    const_over = evaluate_matrix(
        const_spec, ScoringInput("construction_demerit", {"fence_missing": Decimal("12")})
    )
    assert const_over.demerit_triggered is True

    # 5d. 環境教育法 §23 / 講習辦法 §8 時數
    cap = Decimal("100000")
    assert compute_lecture_hours(10000, cap) == (1, 1)
    assert compute_lecture_hours(35000, cap) == (2, 2)
    assert compute_lecture_hours(50000, cap) == (4, 4)
    assert compute_lecture_hours(80000, cap) == (8, 8)
    assert compute_lecture_hours(35000, cap, repeat_within_year=True) == (4, 4)
    assert compute_lecture_hours(80000, cap, repeat_within_year=True) == (8, 8)


def test_disposition_audit_d1_d2_d3_and_db_matrix_roundtrip(conn: Conn, tmp_path: Path) -> None:
    # CQ D1–D3 檢核
    audit_ok: DispositionAuditResult = audit_disposition(
        citations=(
            DispositionCitation("水污法第7條第1項", "duty_basis", in_force=True),
            DispositionCitation("放流水標準第2條", "delegated_command", in_force=True),
            DispositionCitation("水污法第40條第1項", "sanction_basis", in_force=True),
        ),
        elements=(
            DispositionElement(
                "放流水COD超標", "tier1_elements", "檢測 150 mg/L", "sampling_report"
            ),
        ),
        two_tier_defenses_addressed=True,
        stated_fine=60000,
    )
    assert audit_ok.d1_citations_complete is True
    assert audit_ok.d2_evidence_grounded is True
    assert audit_ok.d3_defense_and_bounds_valid is True

    # DB 裁罰矩陣回寫與載入
    _first(conn, tmp_path, _law_v1())
    prov = conn.execute(
        """
        SELECT p.id, v.content_sha256
          FROM corpus.provision AS p
          JOIN corpus.provision_version AS v ON v.provision_id = p.id
         LIMIT 1
        """
    ).fetchone()
    assert prov is not None
    m_row = conn.execute(
        """
        INSERT INTO scoring.penalty_matrix
            (matrix_code, provision_id, content_sha256, law_title, norm_kind,
             formula_kind, legal_effect, base_amount, statutory_min, statutory_max,
             metadata)
        VALUES
            ('db_water_test', %s, %s, '測試水污裁罰準則', 'delegated_regulation',
             'additive_points_times_base', 'fine', 6000, 60000, 20000000,
             '{"statutory_basis": "水污法第40條"}'::jsonb)
        RETURNING id
        """,
        (prov[0], prov[1]),
    ).fetchone()
    assert m_row is not None
    conn.execute(
        """
        INSERT INTO scoring.matrix_factor
            (matrix_id, factor_code, factor_role, label_zho, statutory_ref, default_value)
        VALUES
            (%s, 'base_pts', 'base_point', '違規態樣點數', '附表一', 10)
        """,
        (m_row[0],),
    )
    conn.commit()

    loaded_matrices = load_matrices(conn, BEFORE)
    assert "db_water_test" in loaded_matrices
    skipped = evaluate(
        conn,
        BEFORE,
        {},
        scoring_inputs=(ScoringInput("missing_matrix", {}),),
    )
    assert skipped.scoring_results == ()
    evaluated = evaluate(
        conn,
        BEFORE,
        {},
        scoring_inputs=(ScoringInput("db_water_test", {}),),
    )
    assert len(evaluated.scoring_results) == 1
    assert evaluated.scoring_results[0].clamped_min == 60000


def test_reasoning_codec_roundtrips_new_proto_fields(conn: Conn, tmp_path: Path) -> None:
    from envcite.ontology.v2 import reasoning_pb2 as pb
    from envcite_ontology import errors

    _first(conn, tmp_path, _law_v1())
    prov = conn.execute(
        "SELECT p.id, v.content_sha256 FROM corpus.provision AS p"
        " JOIN corpus.provision_version AS v ON v.provision_id = p.id LIMIT 1"
    ).fetchone()
    assert prov is not None
    conn.execute(
        """
        INSERT INTO scoring.penalty_matrix
            (matrix_code, provision_id, content_sha256, law_title, norm_kind,
             formula_kind, legal_effect, base_amount, statutory_min, statutory_max, metadata)
        VALUES
            ('codec_water', %s, %s, '水污準則', 'delegated_regulation',
             'additive_points_times_base', 'fine', 6000, 60000, 20000000, '{}'::jsonb)
        """,
        (prov[0], prov[1]),
    )
    conn.commit()

    req = pb.EvaluateRequest(
        conduct_on="2019-01-01",
        decided_on="2019-01-01",
        facts=[
            pb.Fact(
                concept="cod",
                values=[pb.FactValue(number=pb.Decimal(value="150000"))],
                unit="ug/L",
            )
        ],
        administrative_penalty_facts=pb.AdministrativePenaltyFacts(
            negligence=True,
            fact_mistake=True,
        ),
        timeline=pb.PerOccasionTimeline(
            cure_notice_served_on="2019-01-01",
            cure_deadline="2019-01-10",
            reinspection_on="2019-01-05",
        ),
        scoring_inputs=[pb.ScoringInput(matrix_code="codec_water")],
        disposition_audit=pb.DispositionAuditInput(
            citations=[
                pb.DispositionCitation(
                    citation_text="水污法第7條", role="duty_basis", in_force=True
                ),
                pb.DispositionCitation(
                    citation_text="水污法第40條", role="sanction_basis", in_force=True
                ),
            ],
            elements=[
                pb.DispositionElement(
                    element_name="COD超標",
                    tier="tier1_elements",
                    fact_summary="150 mg/L",
                    evidence_kind="sampling_report",
                )
            ],
            two_tier_defenses_addressed=True,
            stated_fine=60000,
            scoring_matrix_code="codec_water",
        ),
    )
    v = errors.Validator()
    case = request_codec.case_facts(req, v, None, None, None)
    units = request_codec.fact_units(req)
    tl = request_codec.timeline(req, v)
    sc = disposition_codec.scoring_inputs(req, v)
    v.check()
    assert case["ap_fact_mistake"] == 1.0
    assert units == {"cod": "ug/L"}
    assert tl is not None and tl.reinspection_on == date(2019, 1, 5)
    res = evaluate(
        conn,
        BEFORE,
        case,
        fact_units=units,
        timeline=tl,
        scoring_inputs=sc,
    )
    audit = disposition_codec.disposition_audit(req, res.scoring_results)
    out = outcome_codec.outcome(res, {}, audit_result=audit)
    assert len(out.scoring_results) == 1
    assert out.HasField("disposition_audit")
    assert out.disposition_audit.d1_citations_complete is True
    assert out.disposition_audit.d2_evidence_grounded is True
