"""有項、款的條：條本身沒有內文，內文在子單元；使用者看到的全文要把子單元合起來。"""

from pathlib import Path

from canon.db import Conn, scalar
from canon.law import importer
from canon.law.moj import Moj
from canon.store.canon_store import CanonStore
from tests.sample import _law, stored_snapshot

HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
FIRST = "事業應設置處理設施。"
SECOND = "前項設施應定期檢查。"


def _iri(conn: Conn, path: str) -> str:
    return scalar(
        conn,
        "SELECT corpus.version_iri(v.id) FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE p.path = %s AND v.superseded_at IS NULL",
        (path,),
        str,
    )


def test_an_article_with_paragraphs_reads_as_its_paragraphs_together(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    """引用抽屜依 IRI 取條文：條的那一列沒有內文時，不能回空字串，畫面會說沒有可顯示的內容。"""
    law = _law("T0000001", "廢水管理法", HISTORY, "20180613", [("1", f"{FIRST}\r\n{SECOND}")])
    stored_snapshot(conn, tmp_path, [law])
    importer.run(conn, tmp_path, Moj())
    conn.commit()
    assert (
        scalar(
            conn,
            "SELECT count(*) FROM corpus.provision_version AS v"
            "  JOIN corpus.provision AS p ON p.id = v.provision_id"
            " WHERE p.path = 'art_1' AND v.content IS NULL",
            None,
            int,
        )
        == 1
    )  # 前提：這一條的內文真的在子單元

    with CanonStore.from_dsn(dsn) as store:
        article = store.laws.get_article_by_iri(_iri(conn, "art_1"))
        paragraph = store.laws.get_article_by_iri(_iri(conn, "art_1/para_2"))

    assert article is not None and article.content == f"{FIRST}\n{SECOND}"
    assert paragraph is not None and paragraph.content == SECOND  # 單元本身有內文，照舊
