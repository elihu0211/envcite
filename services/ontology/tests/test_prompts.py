"""Prompts are Jinja2 templates; these tests pin the template mechanics, not the prompt wording."""

import pytest

from envcite_ontology.claims import LEGAL_EFFECTS, TRIGGER_KINDS
from envcite_ontology.verification import prompts

TEXT = "原文 {braces} 與 {{double}} {% raw %}"


def _extract(**override: object) -> str:
    args: dict[str, object] = {
        "law": "L0001",
        "path": "art_7/para_1",
        "text": TEXT,
        "context": "",
        "defined": None,
        "punished_by": (),
        "review": (),
    }
    return prompts.extract(**(args | override))  # type: ignore[arg-type]


def test_substituted_values_are_never_parsed_as_template_syntax() -> None:
    assert TEXT in _extract()
    assert TEXT in prompts.reread(text=TEXT, context="")


def test_the_enumerations_come_from_the_claims_contract() -> None:
    out = _extract()
    assert "|".join(LEGAL_EFFECTS) in out and "|".join(TRIGGER_KINDS) in out


def test_optional_blocks_appear_only_when_there_is_something_to_say() -> None:
    bare = _extract()
    for marker in (
        "上層條文（只供理解",
        "本法已定義的用語（代碼：名稱）",
        "以「違反」引用",
        "審查意見如下",
    ):
        assert marker not in bare
    full = _extract(
        context="上層",
        defined={"water": "水體"},
        punished_by=("art_1", "art_2"),
        review=("缺期限",),
    )
    assert "上層條文（只供理解" in full and "- water：水體" in full
    assert "（art_1、art_2）以「違反」引用" in full and "- 缺期限" in full


def test_the_two_definition_prompts_share_the_clause_intro_and_differ_in_role_and_label() -> None:
    extractor = prompts.definitions(law="L", path="art_2", text="t", context="")
    reader = prompts.reread(text="t", context="")
    intro = "下面是「本法用語，定義如下」之下的一款原文。"
    assert intro in extractor and intro in reader
    assert extractor.startswith("你是法規標註器。") and reader.startswith("你是法規閱讀者。")
    assert "原文（L art_2）：\nt" in extractor and "\n原文：\nt" in reader


def test_repair_wraps_the_original_prompt_with_the_previous_output_and_issues() -> None:
    out = prompts.repair(prompt="原提示", previous="上次輸出", issues=["一", "二"])
    assert out.startswith("原提示\n\n你上一次的輸出：\n上次輸出\n\n")
    assert out.endswith("只輸出 JSON：\n一\n二\n")


def test_candidates_are_listed_one_per_line() -> None:
    out = prompts.choose(
        text="罰則",
        candidates=[prompts.Candidate(3, "art_7", "甲"), prompts.Candidate(4, "art_8", "乙")],
    )
    assert out.endswith("候選陳述：\n[3] art_7：甲\n[4] art_8：乙\n")


def test_the_concepts_must_be_lines_not_one_joined_string() -> None:
    assert prompts.write_tests(text="t", concepts=["a：甲", "b：乙"]).endswith(
        "可用的概念：\na：甲\nb：乙\n"
    )
    with pytest.raises(TypeError):
        prompts.write_tests(text="t", concepts="a：甲\nb：乙")
