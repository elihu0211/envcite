"""The pipeline as a scheduled job: it finds its own work, converges, and runs one at a time."""

from pathlib import Path

import psycopg

from env_law_ontology.verification.run import pending_laws, run, try_lock
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import DETERMINISTIC, EXTRACTED, Conn, FakeModel

TEXT = "排放廢水者應符合標準"


def _load_law(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))


def _run(conn: Conn, answer: object, version: str = "1") -> FakeModel:
    model = FakeModel(answer)
    run(conn, model, [], [LAW], agent_version=version, required=DETERMINISTIC)
    return model


def test_a_law_with_unextracted_units_is_pending_until_the_model_has_answered(
    conn: Conn, tmp_path: Path
) -> None:
    _load_law(conn, tmp_path)
    assert pending_laws(conn, "fake:model", "1") == [LAW]

    _run(conn, EXTRACTED)

    assert pending_laws(conn, "fake:model", "1") == []


def test_a_new_extractor_version_makes_the_law_pending_again(conn: Conn, tmp_path: Path) -> None:
    _load_law(conn, tmp_path)
    _run(conn, EXTRACTED, version="1")

    assert pending_laws(conn, "fake:model", "2") == [LAW]


def test_a_unit_the_model_answered_with_nothing_is_not_asked_again(
    conn: Conn, tmp_path: Path
) -> None:
    """沒有陳述的答案也是答案：否則定義條文每次排程都再付一次模型費用。"""
    _load_law(conn, tmp_path)
    first = _run(conn, {"concepts": [], "statements": []})
    second = _run(conn, {"concepts": [], "statements": []})

    assert len(first.prompts) == 2  # 兩個單元各問一次
    assert second.prompts == []
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0
    assert _scalar(conn, "SELECT count(*) FROM annotation.extraction_attempt") == 2
    assert pending_laws(conn, "fake:model", "1") == []


def test_an_answer_that_is_not_json_is_retried_next_time(conn: Conn, tmp_path: Path) -> None:
    _load_law(conn, tmp_path)
    _run(conn, "not json")

    assert _scalar(conn, "SELECT count(*) FROM annotation.extraction_attempt") == 0
    assert pending_laws(conn, "fake:model", "1") == [LAW]


def test_changed_text_puts_the_unit_back_on_the_list(conn: Conn, tmp_path: Path) -> None:
    _load_law(conn, tmp_path)
    _run(conn, {"concepts": [], "statements": []})
    assert pending_laws(conn, "fake:model", "1") == []

    # 內容雜湊變了，舊的抽取紀錄不再對得上現行文字。
    conn.execute(
        "UPDATE annotation.extraction_attempt SET content_sha256 = decode(repeat('ab', 32), 'hex')"
    )
    conn.commit()

    assert pending_laws(conn, "fake:model", "1") == [LAW]


def test_the_limit_caps_how_many_laws_one_run_takes(conn: Conn, tmp_path: Path) -> None:
    _load_law(conn, tmp_path)

    assert pending_laws(conn, "fake:model", "1", limit=0) == []
    assert pending_laws(conn, "fake:model", "1", limit=1) == [LAW]


def test_only_one_run_holds_the_lock(dsn: str) -> None:
    with psycopg.connect(dsn) as first, psycopg.connect(dsn) as second:
        assert try_lock(first) is True
        assert try_lock(second) is False
        first.close()  # 鎖隨連線結束釋放
        assert try_lock(second) is True
