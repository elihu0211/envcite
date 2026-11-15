"""原生黃金題庫：條文結構的解析、各題型的產生器、查證，以及產出能直接餵進既有的量測流程。

產生器吃一個「條文代號 → 條文」的字典，所以用一小撮手寫的條文就能驗證規則；對真實語料的查證是
`tools/native_golden.py verify` 的事。
"""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

from tests.sample import AIR

TOOLS = Path(__file__).resolve().parents[1] / "tools"
Conn = psycopg.Connection[tuple[object, ...]]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    sys.path.insert(0, str(TOOLS))
    return _load("native_golden")


def _article(tool: ModuleType, pcode: str, title: str, number: str, text: str, kind: str) -> Any:
    return tool.Article(pcode, title, number, text, kind)


def _corpus(tool: ModuleType) -> dict[str, Any]:
    air, order = "A0000001", "B0000001"
    articles = [
        _article(tool, air, "空氣污染防制法", "1", "為防制空氣污染，制定本法。", "statute"),
        _article(
            tool,
            air,
            "空氣污染防制法",
            "2",
            "本法用詞，定義如下：\n一、空氣污染物：指空氣中足以直接或間接妨害人體健康之物質。\n"
            "二、排放標準：指排放管道所排放空氣污染物之限值。",
            "statute",
        ),
        _article(tool, air, "空氣污染防制法", "5", "事業應申報排放量。", "statute"),
        _article(tool, air, "空氣污染防制法", "7", "事業應設置防制設備。", "statute"),
        _article(tool, air, "空氣污染防制法", "8", "前條之設備，準用第七條之規定。", "statute"),
        _article(
            tool,
            air,
            "空氣污染防制法",
            "20",
            "違反第五條規定者，處新臺幣一萬元以上罰鍰。",
            "statute",
        ),
        _article(
            tool,
            air,
            "空氣污染防制法",
            "21",
            "違反水污染防治法第七條規定者，處新臺幣一萬元以上罰鍰。",
            "statute",
        ),
        _article(
            tool,
            order,
            "空氣污染防制設備辦法",
            "1",
            "本辦法依空氣污染防制法第五條第二項規定訂定之。",
            "regulation",
        ),
    ]
    return {a.key: a for a in articles}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("一", 1),
        ("十", 10),
        ("十一", 11),
        ("三十一", 31),
        ("一百零五", 105),
        ("一千二百", 1200),
        ("二〇一", 201),
        ("31", 31),
        ("３１", 31),
        ("", None),
        ("甲", None),
    ],
)
def test_chinese_numerals_read_like_the_corpus_article_numbers(
    tool: ModuleType, text: str, expected: int | None
) -> None:
    assert tool.zh_to_int(text) == expected


def test_a_sub_article_is_written_the_way_the_corpus_numbers_it(tool: ModuleType) -> None:
    assert tool.article_number("三十一", "一") == "31-1"
    assert tool.article_number("六", None) == "6"
    assert tool.article_number("六", "甲") is None
    assert tool.refs_of("依第三十一條之一與第七條、第七條規定") == ["31-1", "7"]


@pytest.mark.parametrize(
    ("text", "refs"),
    [
        ("違反第六條規定者，處新臺幣一千元罰鍰。", ["6"]),
        ("違反本法第七條規定，處新臺幣一千元罰鍰。", ["7"]),
        ("違反第三十一條之一第二項、第三十二條規定者，處一年以下有期徒刑。", ["31-1", "32"]),
        ("違反第四條或第五條第一項規定者，科新臺幣一千元罰鍰。", ["4", "5"]),
    ],
)
def test_penalty_sentences_that_cite_the_laws_own_articles_are_read(
    tool: ModuleType, text: str, refs: list[str]
) -> None:
    ((_, found),) = tool.violation_refs(text)

    assert found == refs


@pytest.mark.parametrize(
    "text",
    [
        "違反水污染防治法第七條規定者，處新臺幣一千元罰鍰。",  # 別部法規
        "違反本辦法第五條規定者，處新臺幣一千元罰鍰。",  # 本辦法不是法律自己
        "違反第三條至第五條規定者，處新臺幣一千元罰鍰。",  # 範圍不收
        "違反第六條規定時，應通知主管機關。",  # 後面沒有處罰
        "違反前項規定者，處新臺幣一千元罰鍰。",  # 沒有條號
    ],
)
def test_sentences_the_grammar_cannot_read_exactly_are_left_out(
    tool: ModuleType, text: str
) -> None:
    assert tool.violation_refs(text) == []


def test_definitions_are_read_from_a_defining_article_only(tool: ModuleType) -> None:
    corpus = _corpus(tool)

    assert [t for t, _ in tool.definitions_of(corpus["A0000001-art-2"])] == [
        "空氣污染物",
        "排放標準",
    ]
    assert tool.definitions_of(corpus["A0000001-art-5"]) == []


def test_definition_questions_come_in_two_forms_with_the_same_answer(tool: ModuleType) -> None:
    items = tool.gen_definitions(_corpus(tool))

    by_kind = {i["kind"]: i for i in items if i["group"] == "排放標準"}
    assert by_kind["definition_term"]["question"] == "排放標準"
    assert by_kind["definition_question"]["question"] == "排放標準的定義是什麼？"
    assert by_kind["definition_term"]["expected"] == by_kind["definition_question"]["expected"]
    assert [e["article"] for e in by_kind["definition_term"]["expected"]] == ["A0000001-art-2"]


def test_a_duty_question_is_answered_by_the_penalty_article_that_cites_it(
    tool: ModuleType,
) -> None:
    (item,) = tool.gen_duty_penalty(_corpus(tool))

    assert item["question"] == "違反空氣污染防制法第5條規定，會受到什麼處罰？"
    assert item["bucket"] == "multi_hop"
    assert [e["article"] for e in item["expected"]] == ["A0000001-art-20"]
    assert item["expected"][0]["evidence"] == "違反第五條規定"


def test_an_order_is_asked_about_by_name_and_answered_by_the_authorizing_article(
    tool: ModuleType,
) -> None:
    (item,) = tool.gen_delegation(_corpus(tool))

    assert item["question"] == "「空氣污染防制設備辦法」是依哪一部法律的哪一條訂定的？"
    assert [e["article"] for e in item["expected"]] == ["A0000001-art-5"]
    assert "空氣污染防制法" not in item["question"]  # 題目不洩漏答案的法規名稱


def test_an_unknown_parent_law_means_no_question(tool: ModuleType) -> None:
    corpus = _corpus(tool)
    del corpus["A0000001-art-1"]
    corpus["B0000001-art-1"] = _article(
        tool,
        "B0000001",
        "空氣污染防制設備辦法",
        "1",
        "本辦法依不存在的法第五條規定訂定之。",
        "regulation",
    )

    assert tool.gen_delegation(corpus) == []


def test_mutatis_mutandis_asks_which_article_is_applied(tool: ModuleType) -> None:
    (item,) = tool.gen_applies(_corpus(tool))

    assert item["question"] == "空氣污染防制法第8條準用了哪一條的規定？"
    assert [e["article"] for e in item["expected"]] == ["A0000001-art-7"]


def test_absent_terms_are_only_those_no_current_article_mentions(tool: ModuleType) -> None:
    corpus = _corpus(tool)
    first = tool.ABSENT_CANDIDATES[0]
    corpus["A0000001-art-1"] = _article(
        tool, "A0000001", "空氣污染防制法", "1", f"本法不得{first}。", "statute"
    )

    keep, present = tool.absent_terms(corpus)

    assert first in present and first not in keep
    assert len(keep) == len(tool.ABSENT_CANDIDATES) - 1
    items = tool.gen_absent(corpus)
    assert {i["bucket"] for i in items} == {"negative"}
    assert all(i["expected"] == [] for i in items)


def test_the_split_follows_the_group_so_related_questions_stay_on_one_side(
    tool: ModuleType,
) -> None:
    items = tool.gen_definitions(_corpus(tool))

    sides: dict[str, set[str]] = {}
    for i in items:
        sides.setdefault(i["group"], set()).add(tool.split_of(i["group"]))
    assert all(len(s) == 1 for s in sides.values())
    assert tool.split_of("排放標準") == tool.split_of("排放標準")


def _doc(tool: ModuleType, articles: dict[str, Any]) -> dict[str, Any]:
    items = [i for generate in tool.GENERATORS for i in generate(articles)]
    for i in items:
        i["split"] = tool.split_of(i["group"])
    return {
        "schema": tool.SCHEMA,
        "as_of": "2026-01-01",
        "snapshots": [{"feed": "moj_law", "data_date": "2026-01-01", "sha256": "0" * 64}],
        "items": sorted(items, key=lambda i: i["id"]),
    }


def test_a_freshly_built_set_verifies_clean(tool: ModuleType) -> None:
    articles = _corpus(tool)

    assert tool.verify(_doc(tool, articles), articles, 3) == []


def test_verify_catches_a_wrong_answer_a_changed_corpus_and_a_present_absent_term(
    tool: ModuleType,
) -> None:
    articles = _corpus(tool)
    doc = _doc(tool, articles)
    duty = next(i for i in doc["items"] if i["kind"] == "duty_penalty")
    duty["expected"][0]["article"] = "A0000001-art-999"
    absent = next(i for i in doc["items"] if i["kind"] == "absent_term")
    articles["A0000001-art-1"] = _article(
        tool, "A0000001", "空氣污染防制法", "1", f"本法不得{absent['group']}。", "statute"
    )

    problems = tool.verify(doc, articles, 0)

    assert any("A0000001-art-999 不在現行語料" in p for p in problems)
    assert any(absent["id"] in p and "出現在現行條文" in p for p in problems)
    assert any("重建" in p for p in problems)


def test_the_completeness_check_finds_a_penalty_article_the_grammar_missed(
    tool: ModuleType,
) -> None:
    articles = _corpus(tool)
    doc = _doc(tool, articles)
    articles["A0000001-art-22"] = _article(
        tool,
        "A0000001",
        "空氣污染防制法",
        "22",
        "違反本法第五條之規定，仍不改善者，按次處罰。",  # 文法不認得「之規定」，獨立核對看得到
        "statute",
    )

    problems = tool.verify(doc, articles, 0)

    assert any("A0000001-art-22 引用它" in p for p in problems)


def test_derive_writes_the_documents_the_existing_measurement_reads(
    tool: ModuleType, tmp_path: Path
) -> None:
    articles = _corpus(tool)
    path = tmp_path / "native-golden.json"
    path.write_text(json.dumps(_doc(tool, articles), ensure_ascii=False, indent=1) + "\n")
    gs = sys.modules["golden_set"]

    qrels_path, split_path = tool.derive(path, json.loads(path.read_text()))

    qrels, split = gs.read(qrels_path), gs.read(split_path)  # 封存的雜湊對得上
    assert qrels["golden_sha256"] == split["golden_sha256"] == gs.file_sha256(path)
    items = {i["id"]: i for i in json.loads(path.read_text())["items"]}
    negatives = {q for q, i in items.items() if i["bucket"] == "negative"}
    assert negatives and not negatives & qrels["qrels"].keys()  # 無答案題不進 qrels
    assert all(g == gs.UPSTREAM_GRADE for docs in qrels["qrels"].values() for g in docs.values())
    assert set(split["dev"]) | set(split["test"]) == set(items)
    assert not set(split["dev"]) & set(split["test"])


def test_the_existing_evaluation_scores_a_native_set_without_a_name_mapping(
    tool: ModuleType, tmp_path: Path
) -> None:
    """題庫的 id、bucket、question 與 derive 的 qrels 就足以讓 `golden_retrieval_eval` 計分。"""
    articles = _corpus(tool)
    path = tmp_path / "native-golden.json"
    doc = _doc(tool, articles)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    qrels_path, split_path = tool.derive(path, doc)
    gs = sys.modules["golden_set"]
    evaluation = _load("golden_retrieval_eval")
    items, _ = evaluation._golden(path)
    qrels_doc, split_doc = gs.read(qrels_path), gs.read(split_path)

    rows = evaluation.questions(items, qrels_doc, gs.subset_ids(split_doc, "dev"))
    rows += evaluation.questions(items, qrels_doc, gs.subset_ids(split_doc, "test"))

    assert {r.id for r in rows} == {i["id"] for i in items}
    assert {r.status for r in rows} <= {"scored", "negative"}  # 沒有 unmapped：答案本來就是條文代號
    assert {r.status for r in rows} == {"scored", "negative"}


def test_articles_are_read_from_the_corpus_by_law_code_and_number(
    tool: ModuleType, conn: Conn, sample: None
) -> None:
    articles = tool.load_articles(conn, date.today().isoformat())

    first = articles[f"{AIR}-art-1"]
    assert (first.pcode, first.number, first.title) == (AIR, "1", "空氣污染防制法")
    assert first.text and first.norm_kind == "statute"
    assert all(k == a.key for k, a in articles.items())
