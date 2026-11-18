"""抽取者宣告概念前先看到方案裡相關的既有概念，同一個意思才不會再得到另一個代碼與名稱。"""

from pathlib import Path

from canon.db import Conn
from canon.verification.context import KnownConcept, known_concepts
from canon.verification.extract import extract_unit
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED, FakeModel

FIRST = "排放廢水者應符合標準"
SECOND = "排放廢水者應申報"


def _seeded(conn: Conn, tmp_path: Path) -> None:
    """第一條已抽取：方案裡有「排放廢水」與「符合標準」兩個概念。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(FIRST, SECOND)))
    extract_unit(conn, FakeModel(EXTRACTED), LAW, "art_1", agent="fake-model", agent_version="1")


def test_a_concept_whose_name_is_in_the_text_is_known(conn: Conn, tmp_path: Path) -> None:
    _seeded(conn, tmp_path)

    known = known_concepts(conn, "env", SECOND)

    assert known == [KnownConcept("discharging", "排放廢水")]


def test_a_text_that_shares_only_a_little_of_a_name_knows_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    _seeded(conn, tmp_path)

    assert known_concepts(conn, "env", "符合規定") == []  # 「符合標準」的字雙連只有一個在
    assert known_concepts(conn, "env", "毫不相干的一句話") == []


def test_the_most_covered_names_come_first_and_the_list_is_capped(
    conn: Conn, tmp_path: Path
) -> None:
    _seeded(conn, tmp_path)

    both = known_concepts(conn, "env", FIRST)

    assert [k.name for k in both] == ["排放廢水", "符合標準"]
    assert [k.name for k in known_concepts(conn, "env", FIRST, limit=1)] == ["排放廢水"]


def test_another_scheme_is_not_offered(conn: Conn, tmp_path: Path) -> None:
    _seeded(conn, tmp_path)

    assert known_concepts(conn, "other_scheme", FIRST) == []


def test_the_extractor_is_shown_only_the_concepts_the_unit_is_about(
    conn: Conn, tmp_path: Path
) -> None:
    _seeded(conn, tmp_path)
    model = FakeModel({"concepts": [], "statements": []})

    extract_unit(conn, model, LAW, "art_2", agent="fake-model", agent_version="1")

    assert "- discharging｜排放廢水" in model.prompts[0]
    assert "- compliant｜符合標準" not in model.prompts[0]


def test_a_unit_with_nothing_known_gets_no_such_block(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(FIRST, SECOND)))
    model = FakeModel({"concepts": [], "statements": []})

    extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert "本方案已有的概念" not in model.prompts[0]


def test_a_concept_declared_again_under_a_new_code_stays_one_concept(
    conn: Conn, tmp_path: Path
) -> None:
    _seeded(conn, tmp_path)
    answer = {
        "concepts": [
            {"code": "discharge_again", "label": "排放廢水"},
            {"code": "report", "label": "申報"},
        ],
        "statements": [
            {
                "id": "s2",
                "quote": SECOND,
                "kind": "prescriptive",
                "deontic": "obligation",
                "conditions": [
                    {
                        "role": "scope",
                        "clause": 1,
                        "concept": "discharge_again",
                        "comparator": "eq",
                        "threshold": 1,
                    },
                    {
                        "role": "duty",
                        "clause": 1,
                        "concept": "report",
                        "comparator": "eq",
                        "threshold": 1,
                    },
                ],
            }
        ],
    }

    out = extract_unit(conn, FakeModel(answer), LAW, "art_2", agent="fake", agent_version="1")

    assert out.loaded is not None and out.loaded.statements == 1
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept WHERE name = '排放廢水'") == 1
