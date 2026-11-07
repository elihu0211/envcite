"""主體槽位的確定性檢查：行為人必須是原文寫出的人，否則記成「未明示」。

案例取自義務重抽的抽查（`eval/duty-reextraction-2026-11-06.md`）：確認的義務裡，
主體被寫成被規範的設施、藥劑、行為，或抽取者憑空補的「任何人」「行為人」。
"""

from typing import Any

import pytest

from envcite_ontology.verification.subject import acceptable, grounded, names_actor, normalize

ART_25 = "前項建築物污水處理設施之建造、管理及清理，應符合中央主管機關及目的事業主管機關之規定。"
ART_7 = "事業、污水下水道系統或建築物污水處理設施，排放廢（污）水於地面水體者，應符合放流水標準。"
ART_8 = "事業、污水下水道系統及建築物污水處理設施產生之污泥，應妥善處理，不得任意放置或棄置。"


@pytest.mark.parametrize(
    ("literal", "quote"),
    [
        ("事業", "事業於設立或變更前，應先檢具水污染防治措施計畫"),
        ("各級主管機關", "各級主管機關依前項規定為查證工作時，其涉及軍事秘密者，應予保密"),
        ("公私場所之所有人", "公私場所之所有人、使用人或管理人，不得規避、妨礙或拒絕"),
        ("使用人", "公私場所之所有人、使用人或管理人，不得規避、妨礙或拒絕"),
        (
            "事業或污水下水道系統（不含公共污水下水道系統）",
            "事業或污水下水道系統（不含公共污水下水道系統）違反本法者，應繳納",
        ),
        ("污水下水道系統", "事業、污水下水道系統應將主管機關核准之資料公開"),
        # 設施本身接著一段行為：法律列舉的受規範者
        ("建築物污水處理設施", ART_7),
        # 與另一個行為人並列
        ("建築物污水處理設施", "不得任意放置或棄置"),
    ],
)
def test_an_actor_the_text_names_is_kept(literal: str, quote: str) -> None:
    source = ART_8 if quote.startswith("不得") else quote
    peers = ["事業", "污水下水道系統", literal]
    assert acceptable(literal, quote, source, peers) is None


@pytest.mark.parametrize(
    ("literal", "quote", "source", "why"),
    [
        # 以被規範的物或行為為主體：助動詞緊接其後
        ("建築物污水處理設施之建造、管理及清理", ART_25, ART_25, "regulated thing"),
        (
            "飲用水設備",
            "經中央主管機關指定公告之飲用水設備，應符合國家標準",
            "經中央主管機關指定公告之飲用水設備，應符合國家標準",
            "regulated thing",
        ),
        (
            "前項水質監測站",
            "必要時，應增加頻率",
            "前項水質監測站採樣頻率，應視污染物項目特性每月或每季一次為原則，必要時，應增加頻率",
            "regulated thing",
        ),
        # 抽取者補的行為人，原文沒有
        (
            "任何人",
            "在水體或其沿岸規定距離內棄置垃圾",
            "在水污染管制區內，不得有下列行為：\n二、在水體或其沿岸規定距離內棄置垃圾",
            "not written",
        ),
        (
            "行為人",
            "廢（污）水排放於土壤者，不得超過標準",
            "廢（污）水排放於土壤者，不得超過標準",
            "not written",
        ),
        # 抽取者直接寫「沒有」
        ("未明示", "應符合國家標準", "應符合國家標準", "unstated"),
    ],
)
def test_a_subject_that_is_the_regulated_thing_or_not_in_the_text_is_refused(
    literal: str, quote: str, source: str, why: str
) -> None:
    reason = acceptable(literal, quote, source)
    assert reason is not None and why in reason


def test_a_trailing_zhe_the_extractor_added_is_not_an_invention() -> None:
    text = "一、使用毒品、藥品或電流捕殺水生物。"
    assert grounded("使用毒品、藥品或電流捕殺水生物者", text)
    assert not grounded("行為人", "在水污染管制區內，不得有下列行為：")


def test_the_actor_endings_do_not_take_the_thing_endings() -> None:
    assert names_actor("事業") and names_actor("直轄市、縣（市）主管機關")
    assert not names_actor("飲用水設備") and not names_actor("廢（污）水")


def _duty(quote: str, subject: Any, **extra: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "id": "s1",
        "quote": quote,
        "kind": "prescriptive",
        "deontic": "obligation",
    }
    if subject is not None:
        s["slots"] = {"subject": subject}
    return s | extra


def test_a_duty_whose_subject_is_refused_becomes_unstated_and_the_rest_is_untouched() -> None:
    out, dropped = normalize(
        [_duty(ART_25, {"literal": "前項建築物污水處理設施之建造、管理及清理"})], ART_25, ""
    )

    assert out[0]["slots"]["subject"] == {"unstated": True}
    assert dropped["s1"][0].endswith("is the regulated thing, not the one who must act")
    assert out[0]["quote"] == ART_25


def test_a_duty_without_any_subject_is_explicitly_unstated() -> None:
    out, _ = normalize([_duty(ART_25, None)], ART_25, "")
    assert out[0]["slots"]["subject"] == {"unstated": True}


def test_the_alternatives_that_pass_stay_and_keep_their_liability() -> None:
    text = "事業，飲用水設備，不得排放"
    subject = [{"literal": "事業", "liability": "act"}, {"literal": "飲用水設備"}]
    out, dropped = normalize([_duty(text, subject)], text, "")

    assert out[0]["slots"]["subject"] == [{"literal": "事業", "liability": "act"}]
    assert list(dropped) == ["s1"]


def test_statements_that_are_not_duties_and_malformed_slots_are_left_alone() -> None:
    sanction = {"id": "p", "quote": "處罰", "kind": "sanction"}
    malformed = _duty(ART_25, None, slots=["事業"])
    out, dropped = normalize([sanction, malformed], ART_25, "")

    assert out == [sanction, malformed]
    assert dropped == {}


def test_context_text_grounds_a_subject_the_unit_itself_omits() -> None:
    """款只有半句話，主體常寫在上層的項；上層寫了就有根據。"""
    out, _ = normalize(
        [_duty("使用農藥或化學肥料，致有污染水體之虞。", {"literal": "各級主管機關"})],
        "使用農藥或化學肥料，致有污染水體之虞。",
        "各級主管機關得禁止下列行為：",
    )
    assert out[0]["slots"]["subject"] == [{"literal": "各級主管機關"}]
