"""義務修復效果量測（tools/duty_effect.py）：盲測、抽樣、判定旗標與檢定規則。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "duty_effect.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("duty_effect_module", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["duty_effect_module"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

TEXT = "在水污染管制區內，不得有下列行為：\n一、使用農藥或化學肥料，致有污染主管機關公告水體之虞。"


def _record(sid: int, pid: int, span: tuple[int, int], **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": sid,
        "provision_id": pid,
        "unit": f"O0040001 art_{pid}",
        "text": TEXT,
        "context": "",
        "span": list(span),
        "quote": TEXT[span[0] : span[1]],
        "round": 0,
        "annotation": "義務方向：禁止\n主體：（空白，未填）",
        "unstated": False,
        "carried": 0,
    }
    return base | extra


# 給判定者看的文字


def test_the_annotation_text_is_the_same_whether_or_not_the_scope_was_carried() -> None:
    carried = [("scope", "在水污染管制區內", False, True), ("duty", "使用農藥", False, False)]
    written = [("scope", "在水污染管制區內", False, False), ("duty", "使用農藥", False, False)]
    one = tool.render_annotation("prohibition", [("事業", False)], carried)
    two = tool.render_annotation("prohibition", [("事業", False)], written)
    assert one == two
    assert "承自" not in one


def test_an_unstated_subject_is_shown_as_the_same_blank_every_time() -> None:
    text = tool.render_annotation("obligation", [("", True)], [])
    assert f"主體：{tool.BLANK_SUBJECT}" in text
    assert "未明示" not in text


def test_the_annotation_has_no_subject_field_when_there_is_none() -> None:
    assert "主體：（沒有主體欄）" in tool.render_annotation("obligation", [], [])


# 匿名編號與抽樣


def test_the_blind_key_does_not_carry_the_version_and_differs_between_runs() -> None:
    a, b = tool.blind_key("pre-1", 7), tool.blind_key("post-1", 7)
    assert a != b
    assert tool.blind_key("pre-1", 7) == a
    assert "pre" not in a and "post" not in b


def test_overlapping_quotes_in_one_unit_are_one_segment() -> None:
    assert tool.segments_of([(0, 10), (5, 15), (20, 30), (30, 40)]) == [0, 0, 1, 2]
    assert tool.segments_of([(20, 30), (0, 10)]) == [1, 0]


def test_the_sample_is_fixed_by_seed_and_version_not_by_row_order() -> None:
    ids = list(range(100))
    one = tool.pick(ids, 30, 7, "post-1")
    assert len(one) == 30
    assert one == tool.pick(reversed(ids), 30, 7, "post-1")
    assert one != tool.pick(ids, 30, 7, "post-2")
    assert one != tool.pick(ids, 30, 8, "post-1")
    assert tool.pick(range(5), 30, 7, "post-1") == set(range(5))


def test_the_items_for_the_judges_say_nothing_about_the_condition() -> None:
    runs = {
        "pre-1": [_record(1, 1, (0, 17)), _record(2, 1, (10, 40), round=2)],
        "post-1": [_record(1, 1, (0, 17), unstated=True, carried=2)],
    }
    items, index = tool.build(runs, 30, 1)
    assert len(items) == 3 == len(index)
    for item in items:
        assert set(item) == {"key", "unit", "text", "context", "quote", "annotation"}
    blob = json.dumps(items, ensure_ascii=False)
    assert "pre" not in blob and "post" not in blob
    pre = [m for m in index.values() if m["condition"] == "pre"]
    assert {m["segment"] for m in pre} == {"pre-1|1|0"}  # 兩段引文重疊，同一段
    assert [m["round"] for m in pre if m["id"] == 2] == [2]
    assert next(m for m in index.values() if m["condition"] == "post")["unstated"] is True


def test_the_items_are_shuffled_by_seed() -> None:
    runs = {"pre-1": [_record(i, i, (0, 17)) for i in range(1, 30)]}
    one = [i["key"] for i in tool.build(runs, 30, 1)[0]]
    assert one == [i["key"] for i in tool.build(runs, 30, 1)[0]]
    assert one != [i["key"] for i in tool.build(runs, 30, 2)[0]]
    assert one != sorted(one)


# 判定者的答案


def test_a_quoted_gap_must_be_found_in_the_text() -> None:
    assert tool.supported("在水污染管制區內", TEXT)
    assert tool.supported("在水污染管制區內，不得有", TEXT)  # 標點不影響
    assert tool.supported("在水汙染管制區內", TEXT)  # 異體字
    assert tool.supported("使用農藥…致有污染", TEXT)  # 省略號分段，每段都要在
    assert not tool.supported("在海洋保護區內", TEXT)
    assert not tool.supported("", TEXT)
    assert not tool.supported("區內", TEXT)  # 只有兩字，太短不足為證
    assert not tool.supported("在水污染管制區內…違反本法", TEXT)  # 有一段找不到


def _answer(**kw: Any) -> dict[str, Any]:
    answer: dict[str, Any] = tool.normalize(kw)
    return answer


def test_a_claimed_missing_condition_counts_only_with_a_verbatim_gap() -> None:
    claimed = _answer(subject="actor", missing_condition=True, missing_text="在水污染管制區內")
    f = tool.flags(claimed, TEXT, "")
    assert f["scope"] and not f["unsupported"]
    invented = _answer(subject="actor", missing_condition=True, missing_text="在海洋保護區內")
    f = tool.flags(invented, TEXT, "")
    assert not f["scope"] and f["unsupported"]
    bare = _answer(subject="actor", missing_condition=True)
    assert tool.flags(bare, TEXT, "")["unsupported"]


def test_a_blank_subject_is_a_defect_only_if_the_actor_is_in_the_text() -> None:
    right = _answer(subject="missed_actor", actor_text="主管機關")
    assert tool.flags(right, TEXT, "")["subject"]
    made_up = _answer(subject="missed_actor", actor_text="地方政府")
    f = tool.flags(made_up, TEXT, "")
    assert not f["subject"] and f["unsupported"]
    assert not tool.flags(_answer(subject="unstated_ok"), TEXT, "")["subject"]


def test_a_thing_as_subject_is_a_defect_and_an_unknown_choice_is_unparsed() -> None:
    assert tool.flags(_answer(subject="thing"), TEXT, "")["subject"]
    assert tool.flags(_answer(subject="invented"), TEXT, "")["subject"]
    f = tool.flags(_answer(subject="nonsense"), TEXT, "")
    assert f["unparsed"] and not f["subject"]


def test_a_truncation_needs_its_own_gap() -> None:
    text = "事業應於期限內改善，並於改善後申報。"
    ok = _answer(subject="actor", truncated=True, truncated_text="並於改善後申報")
    assert tool.flags(ok, text, "")["scope"]
    assert not tool.flags(_answer(subject="actor", truncated=True), text, "")["scope"]


# 一致性與檢定規則


def test_kappa_values() -> None:
    assert tool.kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert tool.kappa([True, True, False, False], [True, False, True, False]) == 0.0
    assert tool.kappa([True, True], [True, True]) is None  # 兩位都全判同一類，沒有定義
    assert tool.kappa([], []) is None


def test_a_difference_needs_no_overlap_and_a_gap_wider_than_the_spread() -> None:
    clear = tool.compare([1, 2, 1], [10, 11, 12])
    assert clear["verdict"] == "有差" and clear["separated"]
    overlap = tool.compare([1, 5, 9], [4, 8, 12])
    assert overlap["verdict"] == "不能區分" and not overlap["separated"]
    narrow = tool.compare([1, 2, 9], [10, 11, 12])  # 不重疊，但差距小於組內範圍
    assert narrow["separated"] and narrow["verdict"] == "不能區分"
    down = tool.compare([10, 11, 12], [1, 2, 1])
    assert down["verdict"] == "有差" and down["diff"] < 0


def _row(key: str, ornith: dict[str, Any], nemotron: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": key,
        "text": TEXT,
        "context": "",
        "answers": {"qwen:ornith-35b": ornith, "nvidia:nemotron": nemotron},
    }


def test_the_report_counts_defects_under_each_rule_and_keeps_the_runs_apart() -> None:
    clean = _answer(subject="actor")
    gap = _answer(subject="actor", missing_condition=True, missing_text="在水污染管制區內")
    thing = _answer(subject="thing")
    rows = [
        _row("a", gap, gap),  # 兩位都判適用範圍瑕疵
        _row("b", thing, clean),  # 只有一位判主體瑕疵
        _row("c", clean, clean),
        _row("d", clean, clean),
    ]
    index = {
        "a": {
            "run": "pre-1",
            "condition": "pre",
            "round": 0,
            "segment": "s1",
            "sampled": True,
            "unstated": False,
        },
        "b": {
            "run": "pre-1",
            "condition": "pre",
            "round": 1,
            "segment": "s2",
            "sampled": True,
            "unstated": False,
        },
        "c": {
            "run": "post-1",
            "condition": "post",
            "round": 0,
            "segment": "s3",
            "sampled": True,
            "unstated": True,
        },
        "d": {
            "run": "post-1",
            "condition": "post",
            "round": 0,
            "segment": "s4",
            "sampled": False,
            "unstated": True,
        },
    }
    judgements = {
        "judges": ["qwen:ornith-35b", "nvidia:nemotron"],
        "prompt_sha256": "x",
        "rows": rows,
    }
    report = tool.report_dict(index, judgements)
    census = report["slices"]["census_all_rounds"]["per_run"]
    assert census["pre-1"]["n"] == 2 and census["post-1"]["n"] == 2
    assert census["pre-1"]["defects"]["both"]["any_defect"] == 1
    assert census["pre-1"]["defects"]["any"]["any_defect"] == 2
    assert census["pre-1"]["defects"]["first"]["subject"] == 1
    assert census["pre-1"]["clean_segments_strict"] == 0
    assert census["pre-1"]["clean_segments_lenient"] == 1  # b 沒有兩位都判
    assert census["post-1"]["unstated"] == 2
    sample = report["slices"]["sample_all_rounds"]["per_run"]
    assert sample["post-1"]["n"] == 1  # d 不在抽樣內
    round0 = report["slices"]["census_round0"]["per_run"]
    assert round0["pre-1"]["n"] == 1
    assert report["agreement"]["n"] == 4
    assert report["agreement"]["scope"]["agree"] == 1.0


# 對照組


def _with_parts(
    record: dict[str, Any],
    subjects: list[tuple[str, bool]],
    conditions: list[tuple[str, str, bool, bool]],
) -> dict[str, Any]:
    parts = {"modality": "prohibition", "subjects": subjects, "conditions": conditions}
    return record | {"parts": parts, "unstated": any(u for _, u in subjects)}


def test_a_carried_scope_and_a_blank_subject_can_each_be_broken_on_purpose() -> None:
    record = _with_parts(
        _record(1, 1, (0, 17)),
        [("", True)],
        [("scope", "在水污染管制區內", False, True), ("duty", "使用農藥", False, False)],
    )
    broken = tool.mutations(record)
    assert set(broken) == {"drop_carried", "subject_as_action"}
    assert "在水污染管制區內" not in broken["drop_carried"]
    assert "使用農藥" in broken["drop_carried"]
    assert "主體：使用農藥" in broken["subject_as_action"]
    assert "在水污染管制區內" in broken["subject_as_action"]  # 只換主體，條件不動


def test_nothing_is_broken_when_there_is_nothing_to_break() -> None:
    record = _with_parts(
        _record(1, 1, (0, 17)), [("事業", False)], [("duty", "使用農藥", False, False)]
    )
    assert tool.mutations(record) == {}


def test_control_items_look_like_any_other_item_and_stay_out_of_the_main_tables() -> None:
    record = _with_parts(
        _record(1, 1, (0, 17)),
        [("", True)],
        [("scope", "在水污染管制區內", False, True), ("duty", "使用農藥", False, False)],
    )
    items, index = tool.build({"post-1": [record]}, 30, 1, controls=True)
    assert len(items) == 3
    for item in items:
        assert set(item) == {"key", "unit", "text", "context", "quote", "annotation"}
    controls = {k: m for k, m in index.items() if "control" in m}
    assert {m["control"] for m in controls.values()} == {"drop_carried", "subject_as_action"}
    assert all(m["of"] in index and "control" not in index[m["of"]] for m in controls.values())
    rows = [
        {"key": it["key"], "text": TEXT, "context": "", "answers": {"a": _answer(subject="actor")}}
        for it in items
    ]
    table = tool.run_table(rows, index, ["a"], sampled_only=False, round0_only=False)
    assert table["post-1"]["n"] == 1  # 對照項目不算


def test_the_controls_report_counts_what_the_judges_caught_and_what_they_wrongly_flagged() -> None:
    gap = _answer(subject="actor", missing_condition=True, missing_text="在水污染管制區內")
    clean = _answer(subject="unstated_ok")
    thing = _answer(subject="thing")
    names = ["a", "b"]
    rows = [
        {"key": "o", "text": TEXT, "context": "", "answers": {"a": clean, "b": clean}},
        {"key": "m1", "text": TEXT, "context": "", "answers": {"a": gap, "b": gap}},
        {"key": "m2", "text": TEXT, "context": "", "answers": {"a": thing, "b": clean}},
    ]
    index = {
        "o": {"run": "post-1"},
        "m1": {"control": "drop_carried", "of": "o", "run": "post-1"},
        "m2": {"control": "subject_as_action", "of": "o", "run": "post-1"},
    }
    got = tool.controls_report(rows, index, names)
    assert got["drop_carried"]["both"] == {"mutated_flagged": 1, "original_flagged": 0}
    assert got["subject_as_action"]["both"]["mutated_flagged"] == 0
    assert got["subject_as_action"]["any"]["mutated_flagged"] == 1
    assert got["subject_as_action"]["first"]["mutated_flagged"] == 1


def test_the_known_defective_units_are_tallied_per_condition() -> None:
    gap = _answer(subject="actor", missing_condition=True, missing_text="在水污染管制區內")
    clean = _answer(subject="actor")
    unit = tool.KNOWN_DEFECT_UNITS[0]
    rows = [
        {"key": "a", "text": TEXT, "context": "", "answers": {"j": gap}},
        {"key": "b", "text": TEXT, "context": "", "answers": {"j": clean}},
        {"key": "c", "text": TEXT, "context": "", "answers": {"j": clean}},
    ]
    meta = {"unit": unit, "round": 0, "sampled": True}
    index = {
        "a": meta | {"condition": "pre", "run": "pre-1"},
        "b": meta | {"condition": "post", "run": "post-1"},
        "c": {"unit": "O0040001 art_1", "condition": "post", "run": "post-1"},
    }
    got = tool.known_units_report(rows, index, ["j"])[unit]
    assert got["pre"] == {
        "confirmed": 1,
        "defect_both": 1,
        "defect_any": 1,
        "runs_with_clean": 0,
    }
    assert got["post"]["confirmed"] == 1 and got["post"]["runs_with_clean"] == 1


def test_wilson_interval_brackets_the_rate_and_stays_in_bounds() -> None:
    low, high = tool.wilson(8, 40)
    assert low < 0.2 < high
    assert tool.wilson(0, 10)[0] == 0.0 and tool.wilson(10, 10)[1] == 1.0
    assert tool.wilson(0, 0) is None


# 缺漏的位置與涵蓋

PARA = "事業排放廢水者，應申請許可。登記事項有變更者，應於變更前申請，並於核准後公告。"


def _flags_for(answer: dict[str, Any], quote: str, annotation: str = "") -> dict[str, bool]:
    got: dict[str, bool] = tool.flags(answer, PARA, "", quote, annotation)
    return got


def test_a_condition_from_another_sentence_is_not_a_missing_condition() -> None:
    answer = _answer(subject="actor", missing_condition=True, missing_text="事業排放廢水者")
    f = _flags_for(answer, "應於變更前申請，並於核准後公告")
    assert not f["scope"] and f["unsupported"]  # 在前一句，隔著句號


def test_a_condition_before_the_quote_in_the_same_sentence_counts() -> None:
    answer = _answer(subject="actor", missing_condition=True, missing_text="登記事項有變更者")
    assert _flags_for(answer, "應於變更前申請，並於核准後公告")["scope"]


def test_a_condition_the_annotation_already_states_does_not_count() -> None:
    answer = _answer(subject="actor", missing_condition=True, missing_text="登記事項有變更者")
    written = "義務方向：義務\n主體：事業\n適用範圍（皆須成立）：登記事項有變更\n義務內容：申請"
    f = _flags_for(answer, "應於變更前申請，並於核准後公告", written)
    assert not f["scope"] and f["unsupported"]


def test_an_upper_level_condition_counts_when_it_is_in_the_context() -> None:
    answer = _answer(subject="actor", missing_condition=True, missing_text="在水污染管制區內")
    f = tool.flags(answer, TEXT, "在水污染管制區內，不得有下列行為：", "使用農藥", "")
    assert f["scope"]


def test_a_truncation_must_come_after_the_quote_in_the_same_sentence() -> None:
    after = _answer(subject="actor", truncated=True, truncated_text="並於核准後公告")
    assert _flags_for(after, "應於變更前申請")["scope"]
    before = _answer(subject="actor", truncated=True, truncated_text="事業排放廢水者")
    f = _flags_for(before, "應於變更前申請")
    assert not f["scope"] and f["unsupported"]
    across = _answer(subject="actor", truncated=True, truncated_text="登記事項有變更者")
    assert not _flags_for(across, "應申請許可")["scope"]  # 隔著句號，是下一句


def test_locating_a_quoted_gap_maps_back_to_the_original_text() -> None:
    start, end = tool.locate("登記事項有變更者", PARA)
    assert PARA[start:end] == "登記事項有變更者"
    start, end = tool.locate("登記事項…應於變更前", PARA)
    assert PARA[start:end] == "登記事項有變更者，應於變更前"
    assert tool.locate("應於變更前…登記事項", PARA) is None  # 順序不對


def test_the_unstated_audit_separates_right_wrong_and_disputed() -> None:
    right = _answer(subject="unstated_ok")
    wrong = _answer(subject="missed_actor", actor_text="主管機關")
    rows = [
        {"key": "a", "text": TEXT, "context": "", "answers": {"x": right, "y": right}},
        {"key": "b", "text": TEXT, "context": "", "answers": {"x": wrong, "y": wrong}},
        {"key": "c", "text": TEXT, "context": "", "answers": {"x": right, "y": wrong}},
        {"key": "d", "text": TEXT, "context": "", "answers": {"x": right, "y": right}},
    ]
    meta = {"condition": "post", "run": "post-1", "unstated": True}
    index = {"a": meta, "b": meta, "c": meta, "d": meta | {"unstated": False}}
    got = tool.unstated_audit(rows, index, ["x", "y"])
    assert got == {"post": {"n": 3, "right": 1, "wrong": 1, "disputed": 1}}
