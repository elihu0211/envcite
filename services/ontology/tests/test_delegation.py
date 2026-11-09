"""授權（規範關係的第一種）：從命令首條抽取、依日解析、確認狀態的守門。

用真實的 PostgreSQL：讀取全在 SQL 函式裡。語料直接寫表（不經匯入器），為了精確控制各版本的
效力區間與標題。
"""

from datetime import date
from typing import Any

import psycopg
import pytest
from psycopg.types.range import Range

from envcite_ontology import delegation
from envcite_ontology.delegation import Citation, parse

Conn = psycopg.Connection[tuple[object, ...]]

D2000, D2005, D2010, D2015 = date(2000, 1, 1), date(2005, 1, 1), date(2010, 1, 1), date(2015, 1, 1)
FIRST = "本辦法依水污染防治法（以下簡稱本法）第七條第二項規定訂定之。"
SECOND = "本辦法依水污染防治法第十條規定訂定之。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FIRST, [Citation("水污染防治法", "art_7/para_2")]),
        ("本標準依規費法第十條第一項規定訂定之。", [Citation("規費法", "art_10/para_1")]),
        ("本辦法依預算法第三十一條規定訂定。", [Citation("預算法", "art_31")]),
        ("本辦法依公路法第二十四條之一之規定訂定之。", [Citation("公路法", "art_24-1")]),
        (
            (
                "本辦法依老人福利法 (以下簡稱本法) 第九條第二項、第十一條第三項及\n"
                "第十二條第三項之規定訂定之。"
            ),
            [
                Citation("老人福利法", "art_9/para_2"),
                Citation("老人福利法", "art_11/para_3"),
                Citation("老人福利法", "art_12/para_3"),
            ],
        ),
        (
            "本辦法依金融控股公司法第五十一條、銀行法第四十五條之一第一項及信託業法第四十二條第三項規定訂定之。",
            [
                Citation("金融控股公司法", "art_51"),
                Citation("銀行法", "art_45-1/para_1"),
                Citation("信託業法", "art_42/para_3"),
            ],
        ),
        (
            "本辦法依毒性及關注化學物質管理法第三十五條第二項及第三項規定訂定之。",
            [
                Citation("毒性及關注化學物質管理法", "art_35/para_2"),
                Citation("毒性及關注化學物質管理法", "art_35/para_3"),
            ],
        ),
        (
            "本辦法依據保險法第一○三條第二項第三款規定訂定之。",
            [Citation("保險法", "art_103/para_2")],
        ),
        ("本辦法依水污染防治法第七條前段規定訂定之。", [Citation("水污染防治法", "art_7")]),
        # 沒有條號、沒有「依…訂定」、或引的是不知哪一條：不猜
        ("本標準依規費法規定訂定之。", []),
        ("本辦法依有關法令規定訂定之。", []),
        ("為辦理本事項，特訂定本辦法。", []),
        ("本辦法所稱主管機關，依本法第七條規定。", []),
    ],
)
def test_parse(text: str, expected: list[Citation]) -> None:
    assert parse(text) == expected


def _id(row: tuple[object, ...] | None) -> int:
    assert row is not None
    return int(str(row[0]))


def _law(
    conn: Conn,
    local_id: str,
    titles: list[tuple[date, date | None, str]],
    *,
    kind: str = "statute",
) -> tuple[int, list[int]]:
    """一部法規與它各版本的標題；回傳（法規 id，各版本的語文版本 id）。"""
    law = _id(
        conn.execute(
            "INSERT INTO corpus.legal_resource"
            " (jurisdiction, id_scheme, local_id, norm_kind, document_type)"
            " VALUES ('TW-CENTRAL', 'moj_pcode', %s, %s, '法律') RETURNING id",
            (local_id, kind),
        ).fetchone()
    )
    expressions = [
        _id(
            conn.execute(
                "INSERT INTO corpus.legal_expression (resource_id, language, version_date, title,"
                " text_period, text_status, text_authority)"
                " VALUES (%s, 'zho', %s, %s, %s, 'available', 'official_db') RETURNING id",
                (law, lo, title, Range(lo, hi, "[)")),
            ).fetchone()
        )
        for lo, hi, title in titles
    ]
    return law, expressions


def _provision(
    conn: Conn,
    law: int,
    path: str,
    versions: list[tuple[int, date, date | None, str]],
    parent: int | None = None,
) -> int:
    """一個條文單元；versions 是（語文版本，起，迄，文字）。"""
    provision = _id(
        conn.execute(
            "INSERT INTO corpus.provision (resource_id, parent_id, provision_type, path)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (law, parent, "paragraph" if parent else "article", path),
        ).fetchone()
    )
    for expression, lo, hi, text in versions:
        conn.execute(
            "INSERT INTO corpus.provision_version"
            " (provision_id, expression_id, resource_id, content, force_period, commencement_rule)"
            " VALUES (%s, %s, %s, %s, %s, CASE WHEN %s < (SELECT version_date"
            "   FROM corpus.legal_expression WHERE id = %s) THEN 'retroactive'"
            "   ELSE 'specified' END::corpus.commencement_rule)",
            (provision, expression, law, text, Range(lo, hi, "[)"), lo, expression),
        )
    return provision


class Corpus:
    """水污染防治法（第 7 條第 2 項自 2000 起、第 10 條 2005–2015 現行）與依它訂定的命令。"""

    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.law, (law_v1,) = _law(conn, "O0040001", [(D2000, None, "水污染防治法")])
        art7 = _provision(conn, self.law, "art_7", [(law_v1, D2000, None, "第七條")])
        self.para2 = _provision(
            conn, self.law, "art_7/para_2", [(law_v1, D2000, None, "授權訂定")], art7
        )
        self.art10 = _provision(conn, self.law, "art_10", [(law_v1, D2005, D2015, "第十條")])
        self.command, (v1, v2) = _law(
            conn,
            "O0040010",
            [(D2000, D2010, "放流水標準"), (D2010, None, "放流水標準")],
            kind="regulation",
        )
        self.art1 = _provision(
            conn,
            self.command,
            "art_1",
            [(v1, D2000, D2010, FIRST), (v2, D2010, None, SECOND)],
        )

    def rows(self, on: date) -> list[tuple[Any, ...]]:
        return self.conn.execute(
            "SELECT cited_law_name, cited_path, law_resource_id, law_provision_id, law_status"
            "  FROM annotation.delegations_at(%s, 'zho', %s)",
            (self.command, on),
        ).fetchall()


@pytest.fixture
def corpus(conn: Conn) -> Corpus:
    return Corpus(conn)


def test_extracts_the_authorizing_article_from_the_first_article(
    conn: Conn, corpus: Corpus
) -> None:
    result = delegation.extract(conn)

    assert (result.written, result.unresolved) == (2, 0)
    assert corpus.rows(date(2003, 1, 1)) == [
        ("水污染防治法", "art_7/para_2", corpus.law, corpus.para2, "in_force")
    ]
    method = conn.execute(
        "SELECT DISTINCT d.method::text FROM annotation.derivation AS d"
        " JOIN annotation.assertion AS a ON a.derivation_id = d.id"
    ).fetchall()
    assert method == [("rule",)]


def test_resolves_by_the_date_asked(conn: Conn, corpus: Corpus) -> None:
    delegation.extract(conn)

    # 命令第 1 條改了文字：授權跟著該日現行的文字走，不固定版本
    assert [r[1] for r in corpus.rows(date(2009, 12, 31))] == ["art_7/para_2"]
    assert [(r[1], r[3], r[4]) for r in corpus.rows(D2010)] == [
        ("art_10", corpus.art10, "in_force")
    ]
    # 法律端該條已不現行：仍回報授權，但標出該日的狀態，授權是否仍成立由讀的一方判斷
    assert [(r[1], r[4]) for r in corpus.rows(date(2016, 1, 1))] == [
        ("art_10", "no_longer_in_force")
    ]
    # 命令尚未生效的日子沒有授權
    assert corpus.rows(date(1999, 1, 1)) == []


def test_lists_the_commands_a_law_authorizes_as_of_a_date(conn: Conn, corpus: Corpus) -> None:
    delegation.extract(conn)

    def commands(on: date) -> list[tuple[Any, ...]]:
        return conn.execute(
            "SELECT command_local_id, law_provision_id, law_status"
            "  FROM annotation.delegated_by(%s, 'zho', %s)",
            (corpus.law, on),
        ).fetchall()

    assert commands(date(2003, 1, 1)) == [("O0040010", corpus.para2, "in_force")]
    assert commands(date(2012, 1, 1)) == [("O0040010", corpus.art10, "in_force")]
    assert commands(date(1999, 1, 1)) == []


def test_a_name_the_corpus_does_not_have_is_unresolved_not_guessed(conn: Conn) -> None:
    law, (v1,) = _law(conn, "O0040001", [(D2000, None, "水污染防治法")])
    _provision(conn, law, "art_7", [(v1, D2000, None, "x")])
    command, (c1,) = _law(conn, "O0040010", [(D2000, None, "某辦法")], kind="regulation")
    _provision(conn, command, "art_1", [(c1, D2000, None, "本辦法依水汙防治法第七條規定訂定之。")])

    result = delegation.extract(conn)

    assert (result.written, result.unresolved) == (1, 1)
    row = conn.execute(
        "SELECT cited_law_name, law_resource_id, law_provision_id, law_status"
        "  FROM annotation.delegations_at(%s, 'zho', %s)",
        (command, D2005),
    ).fetchall()
    assert row == [("水汙防治法", None, None, None)]


def test_a_title_shared_by_two_laws_is_unresolved(conn: Conn) -> None:
    for pcode in ("O0040001", "O0040002"):
        law, (v1,) = _law(conn, pcode, [(D2000, None, "同名法")])
        _provision(conn, law, "art_7", [(v1, D2000, None, "x")])
    command, (c1,) = _law(conn, "O0040010", [(D2000, None, "某辦法")], kind="regulation")
    _provision(conn, command, "art_1", [(c1, D2000, None, "本辦法依同名法第七條規定訂定之。")])

    delegation.extract(conn)

    assert conn.execute(
        "SELECT law_resource_id FROM annotation.delegations_at(%s, 'zho', %s)", (command, D2005)
    ).fetchall() == [(None,)]


def test_a_law_that_lacks_the_cited_article_keeps_the_law_but_not_the_provision(conn: Conn) -> None:
    law, (v1,) = _law(conn, "O0040001", [(D2000, None, "水污染防治法")])
    _provision(conn, law, "art_7", [(v1, D2000, None, "x")])
    command, (c1,) = _law(conn, "O0040010", [(D2000, None, "某辦法")], kind="regulation")
    _provision(
        conn, command, "art_1", [(c1, D2000, None, "本辦法依水污染防治法第九十九條規定訂定之。")]
    )

    delegation.extract(conn)

    assert conn.execute(
        "SELECT law_resource_id, law_provision_id, law_status"
        "  FROM annotation.delegations_at(%s, 'zho', %s)",
        (command, D2005),
    ).fetchall() == [(law, None, None)]


def test_an_old_title_still_finds_the_renamed_law(conn: Conn) -> None:
    law, (v1, v2) = _law(conn, "O0040001", [(D2000, D2010, "舊法名"), (D2010, None, "新法名")])
    _provision(conn, law, "art_7", [(v1, D2000, D2010, "x"), (v2, D2010, None, "y")])
    command, (c1,) = _law(conn, "O0040010", [(D2000, None, "某辦法")], kind="regulation")
    _provision(conn, command, "art_1", [(c1, D2000, None, "本辦法依舊法名第七條規定訂定之。")])

    delegation.extract(conn)

    assert conn.execute(
        "SELECT law_resource_id FROM annotation.delegations_at(%s, 'zho', %s)", (command, D2005)
    ).fetchall() == [(law,)]


def test_a_statute_is_not_a_command(conn: Conn) -> None:
    law, (v1,) = _law(conn, "O0040001", [(D2000, None, "水污染防治法")])
    _provision(conn, law, "art_1", [(v1, D2000, None, "本法依憲法第一百零八條規定訂定之。")])

    assert delegation.extract(conn).written == 0


def test_extraction_is_idempotent_and_a_rejection_stays_rejected(
    conn: Conn, corpus: Corpus
) -> None:
    assert delegation.extract(conn).written == 2
    second = delegation.extract(conn)
    assert (second.written, second.skipped) == (0, 2)

    rejected = _id(
        conn.execute("SELECT id FROM annotation.delegation ORDER BY id LIMIT 1").fetchone()
    )
    conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', '引文不是授權')", (rejected,))

    assert delegation.extract(conn).written == 0
    assert conn.execute("SELECT count(*) FROM annotation.delegation_usable").fetchone() == (1,)


def test_a_later_resolved_citation_hides_the_stale_unresolved_one(conn: Conn) -> None:
    command, (c1,) = _law(conn, "O0040010", [(D2000, None, "某辦法")], kind="regulation")
    _provision(
        conn, command, "art_1", [(c1, D2000, None, "本辦法依水污染防治法第七條規定訂定之。")]
    )
    assert delegation.extract(conn).unresolved == 1

    law, (v1,) = _law(conn, "O0040001", [(D2000, None, "水污染防治法")])
    art7 = _provision(conn, law, "art_7", [(v1, D2000, None, "x")])
    assert delegation.extract(conn).written == 1

    assert conn.execute(
        "SELECT law_resource_id, law_provision_id FROM annotation.delegations_at(%s, 'zho', %s)",
        (command, D2005),
    ).fetchall() == [(law, art7)]


def _model_delegation(conn: Conn, corpus: Corpus) -> int:
    """模型抽取、尚未確認的授權（同一處引文，以模型為推導者）。"""
    derivation = _id(
        conn.execute(
            "INSERT INTO annotation.derivation (method, producer, producer_version)"
            " VALUES ('llm', 'alpha:extractor', '1') RETURNING id"
        ).fetchone()
    )
    assertion = _id(
        conn.execute(
            "INSERT INTO annotation.assertion (assertion_kind, derivation_id)"
            " VALUES ('delegation', %s) RETURNING id",
            (derivation,),
        ).fetchone()
    )
    conn.execute(
        "INSERT INTO annotation.delegation (id, command_resource_id, command_provision_id,"
        " command_content_sha256, cited_law_name, cited_path, law_resource_id, law_provision_id)"
        " SELECT %s, %s, v.provision_id, v.content_sha256, '水污染防治法', 'art_7/para_2', %s, %s"
        "   FROM corpus.provision_version AS v WHERE v.provision_id = %s AND v.content = %s",
        (assertion, corpus.command, corpus.law, corpus.para2, corpus.art1, FIRST),
    )
    conn.execute("SELECT annotation.seal(%s)", (assertion,))
    return assertion


def test_a_model_extracted_delegation_is_unusable_until_confirmed(
    conn: Conn, corpus: Corpus
) -> None:
    assertion = _model_delegation(conn, corpus)
    assert corpus.rows(date(2003, 1, 1)) == []

    # 驗證者不得是抽取者；通過所需方法後依接受政策升為已確認
    conn.execute(
        "SELECT annotation.record_verification(%s, 'literal', 'beta:reader', 'pass')", (assertion,)
    )
    assert conn.execute(
        "SELECT * FROM annotation.settle('delegation', ARRAY['literal'])"
    ).fetchone() == (1, 0)

    assert [r[1] for r in corpus.rows(date(2003, 1, 1))] == ["art_7/para_2"]


def test_a_rejected_delegation_is_unusable_even_when_derived_by_rule(
    conn: Conn, corpus: Corpus
) -> None:
    delegation.extract(conn)
    assert len(corpus.rows(date(2003, 1, 1))) == 1
    delegation_id = _id(
        conn.execute(
            "SELECT id FROM annotation.delegation WHERE cited_path = 'art_7/para_2'"
        ).fetchone()
    )

    conn.execute(
        "SELECT annotation.set_confirm_state(%s, 'rejected', '人工複查：非授權')", (delegation_id,)
    )

    assert corpus.rows(date(2003, 1, 1)) == []


def test_the_extractor_cannot_verify_its_own_delegation(conn: Conn, corpus: Corpus) -> None:
    assertion = _model_delegation(conn, corpus)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "SELECT annotation.record_verification(%s, 'literal', 'alpha:extractor', 'pass')",
            (assertion,),
        )


def test_a_sealed_delegation_cannot_change(conn: Conn, corpus: Corpus) -> None:
    delegation.extract(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE annotation.delegation SET cited_path = 'art_8'")


def test_the_sync_role_extracts_and_the_server_role_reads(conn: Conn, corpus: Corpus) -> None:
    conn.commit()
    conn.execute("SET ROLE envcite_ontology_writer")
    assert delegation.extract(conn).written == 2
    conn.execute("SET ROLE envcite_ontology_reader")

    assert [r[1] for r in corpus.rows(date(2003, 1, 1))] == ["art_7/para_2"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM annotation.delegation")
