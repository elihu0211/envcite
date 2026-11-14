"""Acquiring administrative rules from the 行政院公報 open data and the 環境部 interpretation
pages: parsed from the real page shapes, written through the guidance loader one document per
transaction, anchored only to the text in force on the issue date, re-runnable."""

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_jobkit import EndpointError, ExitCode, Policy
from envcite_ontology import guidance_anchor
from envcite_ontology.acquire import gazette, moenv
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.guidance_sync.run import sync
from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import importer
from tests.sample import WATER, sample_laws, stored_snapshot

Conn = psycopg.Connection[tuple[object, ...]]

POLICY = Policy(name="test", max_attempts=1, backoff_base_seconds=0.0)
TODAY = date(2026, 11, 14)


def _no_sleep(_: float) -> None:
    pass


class FakeClient:
    """Pages by URL; unknown pages are empty listings, `fail` URLs are endpoint errors."""

    def __init__(self, pages: dict[str, str], files: dict[str, bytes] | None = None) -> None:
        self.pages = pages
        self.files = files or {}
        self.fail: set[str] = set()
        self.requested: list[str] = []

    def text(self, url: str) -> str:
        self.requested.append(url)
        if url in self.fail:
            raise EndpointError(url)
        return self.pages.get(url, "")

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        import hashlib

        self.requested.append(url)
        data = self.files[url]
        dest.write_bytes(data)
        return hashlib.sha256(data).digest(), len(data)


# --- 環境部函釋：頁面形狀取自實頁（2026-11-03），內容縮短 ---------------------------------


def _row(site_id: str, no: str, on: str, ceased: str | None = None) -> str:
    mark = '<span class="label-fei">廢/停</span>' if ceased is not None else ""
    until = f"<div><span><b>停止適用日期：</b></span><span>{ceased}</span></div>" if ceased else ""
    return (
        f"<tr>\n<td>1.</td>\n<td><div><b>標題：</b>{mark}"
        f'<a href="ExecutiveData.aspx?id={site_id}&type=2">標題</a></div>'
        f"<div><b>字號：</b>{no}</div><div><b>日期：</b><span>{on}</span></div>{until}</td>\n</tr>"
    )


def _detail(no: str, issued: str, related: list[str], subject: str = "放流水標準之適用") -> str:
    links = "".join(f'<li><a href="LawContent.aspx?id=FL1">{r}</a></li>' for r in related)
    return (
        '<html><div>瀏覽人數：123</div><table class="table table-bordered tab-edit table-extent">'
        f'<tr><th><span class="lsp2em">標</span>題：</th><td>{subject}</td></tr>'
        "<tr><th>發文機關：</th><td>\n 行政院環境保護署</td></tr>"
        f"<tr><th>發文字號：</th><td>{no}函</td></tr>"
        f"<tr><th>發文日期：</th><td>{issued}</td></tr>"
        '<tr><th><span class="lsp2em">內</span>容：</th><td class="law-content">'
        f"<span>主 &nbsp; &nbsp;旨：{subject}，請 查照。<br>\n"
        "說 &nbsp; &nbsp;明：一、依水污染防治法第 7 &nbsp;條第 1 &nbsp;項規定，"
        "事業排放廢（污）<br>\n"
        "&nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; 水應符合放流水標準。<br>\n"
        "正 &nbsp; &nbsp;本：各縣市環保局</span></td></tr>"
        '<tr><th>相關法規：</th><td><ul id="ctl00_cp_content_ulLegalDataRela">'
        f"{links}</ul></td></tr>"
        "</table></html>"
    )


def _moenv_site() -> FakeClient:
    leaf = moenv.LIST.format(feid="1702", page=1)
    return FakeClient(
        {
            moenv.QUERY: '<a href="ExecutiveList.aspx?feid=17">大氣</a>',
            moenv.LIST.format(feid="17", page=1): (
                '<ul id="ctl00_cp_content_ulcid" class="TypeLi2">'
                '<li><a href="ExecutiveList.aspx?feid=1702">固定源</a></li></ul>'
            ),
            leaf: _row("11", "環署水字第1080000001號函", "108.05.01", ceased="110.01.01")
            + _row("12", "環署水字第0990000002號函", "099.03.01"),
            moenv.DETAIL.format(id="11"): _detail(
                "環署水字第1080000001號",
                "民國 108 年 05 月 01 日",
                ["水污染防治法第7條", "空污法第1條"],
            ),
            moenv.DETAIL.format(id="12"): _detail(
                "環署水字第0990000002號", "民國 99 年 03 月 01 日", ["水污染防治法第7條第1項"]
            ),
        }
    )


def test_moenv_letter_is_restored_from_the_wrapped_transcription() -> None:
    letter = moenv.parse(
        moenv.content(_detail("環署水字第1號", "民國 108 年 05 月 01 日", ["水污染防治法第7條"]))
    )
    assert letter.issuer == "行政院環境保護署"
    assert letter.document_no == "環署水字第1號"
    assert letter.issued_on == date(2019, 5, 1)
    assert letter.subject == "放流水標準之適用，請查照。"
    # 折行接回、數字旁的空白去掉；正本不屬內文
    assert (
        letter.body
        == "說明：一、依水污染防治法第7條第1項規定，事業排放廢（污）水應符合放流水標準。"
    )
    assert letter.related == ("水污染防治法第7條",)


def test_document_number_drops_whitespace_and_the_letter_type() -> None:
    """公報的字號不帶文別、網站的帶「函」：正規化後是同一件公文。"""
    assert moenv.normalize_document_no("環部水字第 115 號函") == "環部水字第115號"


def test_moenv_row_carries_the_cessation() -> None:
    (row,) = moenv.rows(_row("11", "環署水字第 1號函", "108.05.01", ceased="110.01.01"))
    assert row == moenv.Row("11", "環署水字第1號", date(2019, 5, 1), True, date(2021, 1, 1))


def test_listed_articles_are_separate_citations() -> None:
    assert moenv.expand("水污染防治法第4、23、46-1條") == [
        "水污染防治法第4條",
        "水污染防治法第23條",
        "水污染防治法第46-1條",
    ]
    assert moenv.expand("水污染防治法第7條第1項") == ["水污染防治法第7條第1項"]


@pytest.mark.parametrize(
    ("text", "law", "path"),
    [
        ("空氣污染防制法第32條第1項第1款", "空氣污染防制法", "art_32/para_1/pt_1"),
        ("水污染防治法第七條之一第二項", "水污染防治法", "art_7-1/para_2"),
        ("廢棄物清理法第63-1條", "廢棄物清理法", "art_63-1"),
        ("廢棄物清理法", "廢棄物清理法", None),
    ],
)
def test_related_law_citation(text: str, law: str, path: str | None) -> None:
    assert moenv.citation(text) == moenv.Citation(law, path)


def test_moenv_sync_anchors_to_the_text_in_force_on_the_issue_date(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _moenv_site()
    report = sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    assert report.exit_code == ExitCode.OK
    assert (report.listed, report.documents, report.cessations) == (2, 2, 1)
    # 108 年的函：第 7 條展開為兩項各一列；99 年的函：當時沒有現行版本（語料只有 107 年起的文字）
    anchored = conn.execute(
        "SELECT d.document_no, p.path FROM guidance.interpretation AS i"
        "  JOIN guidance.document AS d ON d.id = i.document_id"
        "  JOIN corpus.provision AS p ON p.id = i.provision_id ORDER BY 1, 2"
    ).fetchall()
    assert anchored == [
        ("環署水字第1080000001號", "art_7/para_1"),
        ("環署水字第1080000001號", "art_7/para_2"),
    ]
    assert report.problems == {"law_unresolved": 1, "no_version_at_issue": 1}
    assert conn.execute("SELECT ceased_on FROM guidance.cessation").fetchall() == [
        (date(2021, 1, 1),)
    ]
    snapshot = conn.execute(
        "SELECT location FROM source.snapshot WHERE feed = 'moenv_guidance' LIMIT 1"
    ).fetchone()
    assert snapshot is not None and "瀏覽人數" not in (tmp_path / str(snapshot[0])).read_text()

    again = sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    assert (again.listed, again.documents) == (0, 0)


def test_moenv_rescan_never_rewrites_a_loaded_letter(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _moenv_site()
    sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    derivations = conn.execute("SELECT count(*) FROM annotation.derivation").fetchone()

    site.pages[moenv.DETAIL.format(id="12")] = _detail(
        "環署水字第0990000002號", "民國 99 年 03 月 01 日", [], subject="網站改了轉錄"
    )
    report = sync(conn, "moenv", tmp_path, rescan=True, client=site, policy=POLICY, sleep=_no_sleep)
    assert (report.documents, report.repeated) == (0, 1)
    assert report.problems["content_changed"] == 1
    assert conn.execute(
        "SELECT subject FROM guidance.document WHERE document_no = '環署水字第0990000002號'"
    ).fetchone() == ("放流水標準之適用，請查照。",)
    # 沿用既有公文不留下新的推導紀錄
    assert conn.execute("SELECT count(*) FROM annotation.derivation").fetchone() == derivations


def test_moenv_isolated_pages_beyond_tolerance_fail_the_run(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _moenv_site()
    site.fail.add(moenv.DETAIL.format(id="12"))
    report = sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    assert (report.dead_lettered, report.documents) == (1, 1)
    assert report.exit_code == ExitCode.FAILED


# --- 行政院公報：XML 形狀取自實包（115-10_01.zip），內容縮短 ---------------------------------


def _record(
    meta: str,
    large: str,
    title: str,
    no: str = "環部水字第1150000001號",
    *,
    on: str = "115年10月1日",
    subject: str = "訂定「放流水標準適用原則」。",
    body: str = "訂定「放流水標準適用原則」，並自即日生效。",
) -> str:
    return f"""<Record>
<MetaId><![CDATA[{meta}]]></MetaId>
<Doc_Style_LName><![CDATA[{large}]]></Doc_Style_LName>
<PubGovName><![CDATA[環境部]]></PubGovName>
<Date_Created><![CDATA[中華民國{on}]]></Date_Created>
<GazetteId><![CDATA[{no}]]></GazetteId>
<Title><![CDATA[{title}]]></Title>
<ThemeSubject><![CDATA[{subject}]]></ThemeSubject>
<GazetteHTML><![CDATA[https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032182/ch04/Eg.htm]]></GazetteHTML>
<HTMLContent><![CDATA[<div class="information"><div class="announcement">
<span>中華民國115年10月1日</span></div></div>
<p class="line2_indent">{body}</p>
<p><span class="title">部長</span>　某甲</p>
<p class="attachment">附件（<a href="images/Eg01.pdf">請參見PDF</a>）</p>]]></HTMLContent>
</Record>"""


def _pack(*records: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "115-10-01/115-10-01.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n<Gazette>' + "".join(records) + "</Gazette>",
        )
        z.writestr("115-10-01/1.pdf", b"%PDF")
    return buf.getvalue()


def _gazette_site(pack: bytes | None = None) -> FakeClient:
    if pack is None:
        pack = _pack(
            _record("1", "行政規則", "環境部令：訂定「放流水標準適用原則」"),
            _record("2", "法規", "環境部令：修正「放流水標準」"),
            _record(
                "3", "行政規則", "環境部令：訂定「水污染裁罰疑義處理原則」", no="環部水字第2號"
            ),
        )
    site = FakeClient({gazette.MONTH_LIST: ""})
    _add_pack(site, "115-10-01.zip", pack)
    return site


def _add_pack(site: FakeClient, name: str, pack: bytes) -> None:
    """清單頁多一個包；歷史月包與本月每日包都列在本月清單頁上即可（測試不分）。"""
    site.pages[gazette.MONTH_LIST] += (
        f'<a href="fileView.do?fileType=zipPath&amp;fileName={name}&amp;attached'
        f'&amp;zipfile={name}">{name}</a>'
    )
    url = f"{gazette.BASE}fileView.do?fileType=zipPath&fileName={name}&attached&zipfile={name}"
    site.files[url] = pack


def test_gazette_pack_reads_only_administrative_rules(tmp_path: Path) -> None:
    (tmp_path / "p.zip").write_bytes(
        _pack(_record("1", "行政規則", "t"), _record("2", "法規", "t"))
    )
    data_date, (rule,) = gazette.read(tmp_path / "p.zip")
    assert data_date == date(2026, 10, 1)
    assert rule.issued_on == date(2026, 10, 1)
    assert rule.published_in == "行政院公報第32卷第182期"
    # 發文機關與字號區塊、署名、附件連結不屬內文
    assert rule.body == "訂定「放流水標準適用原則」，並自即日生效。"


@pytest.mark.parametrize(
    ("title", "classification"),
    [
        ("環境部令：訂定「放流水標準適用原則」", "interpretation"),
        ("環境部令：修正「違反噪音管制法案件裁罰基準」", "discretion_standard"),
        ("環境部令：修正「違反水污染防治法罰鍰額度裁罰準則」", "delegated_regulation"),
    ],
)
def test_gazette_rule_kind_follows_the_title(title: str, classification: str) -> None:
    assert gazette.classify_title(title) == classification
    if classification == "delegated_regulation":
        with pytest.raises(gazette.DelegatedRegulationTitle):
            gazette.rule_kind(title)
    else:
        assert gazette.rule_kind(title) == classification


def test_gazette_pack_name_bounds_the_range() -> None:
    assert gazette.Pack("115-10_02.zip", "u").covers_from(date(2026, 10, 20))
    assert not gazette.Pack("115-09_04.zip", "u").covers_from(date(2026, 10, 1))
    assert not gazette.Pack("115-10-01.zip", "u").covers_from(date(2026, 10, 2))


def test_gazette_sync_writes_rules_and_reports_what_it_could_not(
    conn: Conn, tmp_path: Path
) -> None:
    site = _gazette_site()
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert report.exit_code == ExitCode.OK
    assert (report.listed, report.documents) == (1, 1)
    assert report.problems == {"kind_ambiguous": 1}
    assert conn.execute(
        "SELECT issuer, document_no, issued_on, rule_kind::text, published_in"
        "  FROM guidance.document"
    ).fetchall() == [
        (
            "環境部",
            "環部水字第1150000001號",
            date(2026, 10, 1),
            "interpretation",
            "行政院公報第32卷第182期",
        ),
    ]
    assert conn.execute(
        "SELECT origin_name FROM source.snapshot WHERE feed = 'gazette'"
    ).fetchall() == [("115-10-01.zip",)]

    again = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert again.listed == 0


# --- 法規舊名：語料沿革的更名紀錄 -----------------------------------------------------------


def _import(conn: Conn, root: Path, laws: list[dict[str, Any]]) -> None:
    stored_snapshot(conn, root, laws)
    importer.run(conn, root, Moj())


def _renamed(old: str, law: int) -> list[dict[str, Any]]:
    """樣本語料，其中一部的沿革記載它原名 `old`。"""
    laws = sample_laws()
    laws[law]["LawHistories"] += f"（原名稱：{old}；新名稱：{laws[law]['LawName']}）"
    return laws


def test_an_old_name_from_the_history_resolves_to_the_law(conn: Conn, tmp_path: Path) -> None:
    _import(conn, tmp_path, _renamed("水污染防治條例", 1))
    resolver = guidance_anchor.Resolver(conn)
    assert resolver.aliases == 1
    assert resolver.resolve("水污染防治條例") == guidance_anchor.Resolved(WATER, None)
    found, problems = resolver.anchors("水污染防治條例第7條第1項", date(2019, 5, 1))
    assert [a["path"] for a in found] == ["art_7/para_1"] and problems == []


def test_an_old_name_that_is_another_laws_title_resolves_to_neither(
    conn: Conn, tmp_path: Path
) -> None:
    """舊名被另一部法規用作標題：兩層對到不同的法規，不猜。"""
    _import(conn, tmp_path, _renamed("水污染防治法", 0))
    resolver = guidance_anchor.Resolver(conn)
    assert resolver.resolve("水污染防治法") == guidance_anchor.Resolved(None, "law_ambiguous")
    assert resolver.anchors("水污染防治法第7條", date(2019, 5, 1)) == (
        [],
        [Problem("warning", "law_ambiguous", "水污染防治法第7條")],
    )


# --- 公報行政規則的錨定：內文寫出的依據 -------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "cited"),
    [
        ("核釋水污染防治法第七條第一項所稱「事業」，指下列各款。", ["水污染防治法第七條第一項"]),
        ("為執行「水污染防治法」第七條規定，特訂定本原則。", ["水污染防治法第七條"]),
        ("一、本要點依水污染防治法第40條第2項規定訂定之。", ["水污染防治法第40條第2項"]),
        (
            "為執行水污染防治法第七條第一項及空氣污染防制法第一條規定，特訂定本要點。",
            ["水污染防治法第七條第一項", "空氣污染防制法第一條"],
        ),
        # 規則內容裡的「依…規定」不是依據
        ("二、裁處罰鍰，依水污染防治法第四十條規定辦理。", []),
        # 同一句的「訂定」不是本規則的訂定
        ("採行政委託辦理者，應依水污染防治法第七條規定辦理，並訂定協議書。", []),
        ("依水污染防治法第七條第二項授權訂定之自治法規，收取費用。", []),
        (
            "一、依水污染防治法第七條規定，所訂定之處理程序應納入內控；二、依空氣污染防制法第一條規定訂定之。",
            ["空氣污染防制法第一條"],
        ),
        # 只是提到的條號
        ("違反水污染防治法第七條第一項者，從重處罰。", []),
        # 法名對不到
        ("核釋土壤污染防治法第二條所稱「土壤」。", []),
    ],
)
def test_gazette_basis_is_only_what_the_rule_says_it_rests_on(
    conn: Conn, sample: None, body: str, cited: list[str]
) -> None:
    assert [text for text, _ in guidance_anchor.Resolver(conn).basis(body)] == cited


def test_gazette_rule_is_anchored_to_its_basis_in_force_on_the_issue_date(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    pack = _pack(
        _record(
            "1",
            "行政規則",
            "環境部令：核釋水污染防治法第七條",
            subject="核釋水污染防治法第七條第一項所稱「事業」。",
            body="核釋水污染防治法第七條第一項所稱「事業」，指下列各款。",
        ),
        _record(
            "2",
            "行政規則",
            "行政院環境保護署令：核釋水污染防治法第七條",
            no="環署水字第2號",
            on="100年1月1日",
            subject="核釋水污染防治法第七條。",
            body="核釋水污染防治法第七條所稱「事業」。",
        ),
    )
    site = _gazette_site(pack)
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert report.exit_code == ExitCode.OK
    anchored = conn.execute(
        "SELECT d.document_no, p.path FROM guidance.interpretation AS i"
        "  JOIN guidance.document AS d ON d.id = i.document_id"
        "  JOIN corpus.provision AS p ON p.id = i.provision_id ORDER BY 1, 2"
    ).fetchall()
    # 100 年的令：語料只有 107 年起的文字，不以現行文字代替
    assert anchored == [("環部水字第1150000001號", "art_7/para_1")]
    assert report.problems == {"no_version_at_issue": 1}


# --- 公報以「廢止」停止前一規則 ---------------------------------------------------------------


def _rule(meta: str, on: str, subject: str, issuer_no: str) -> str:
    return _record(
        meta, "行政規則", f"環境部令：{subject}", issuer_no, on=on, subject=subject, body=subject
    )


def _ceased(conn: Conn) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT d.document_no, c.ceased_on, b.document_no FROM guidance.cessation AS c"
        "  JOIN guidance.document AS d ON d.id = c.document_id"
        "  LEFT JOIN guidance.document AS b ON b.id = c.by_document_id ORDER BY 1"
    ).fetchall()


def test_a_gazette_repeal_ceases_every_earlier_publication_of_the_rule(
    conn: Conn, tmp_path: Path
) -> None:
    pack = _pack(
        _rule("1", "114年1月2日", "訂定「甲要點」，並自即日生效。", "甲字第1號"),
        _rule(
            "2",
            "114年6月2日",
            "修正「甲要點」第三點，名稱並修正為「乙要點」，自即日生效。",
            "甲字第2號",
        ),
        _rule(
            "3", "115年10月1日", "廢止「乙要點」，並自中華民國一百十六年一月一日生效。", "甲字第3號"
        ),
        _rule("4", "115年10月1日", "廢止「丙要點」，並自即日生效。", "甲字第4號"),
        _rule("5", "115年10月1日", "廢止「乙要點」第五點，並自即日生效。", "甲字第5號"),
    )
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=_gazette_site(pack),
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert report.exit_code == ExitCode.OK
    assert _ceased(conn) == [
        ("甲字第1號", date(2027, 1, 1), "甲字第3號"),
        ("甲字第2號", date(2027, 1, 1), "甲字第3號"),
    ]
    assert report.cessations == 2
    # 丙要點從沒刊登過；只廢止其中一點的不停止整部
    assert report.problems == {"repeal_unmatched": 1}

    again = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        rescan=True,
        client=_gazette_site(pack),
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert again.cessations == 0 and again.problems == {"repeal_unmatched": 1}
    # 每一步都已提交，沒有留下未結束的交易
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert conn.execute(
        "SELECT count(*) FROM source.import_problem WHERE problem_code = 'repeal_unmatched'"
    ).fetchone() == (1,)


def test_a_repeal_loaded_before_the_rule_it_ends_is_matched_later(
    conn: Conn, tmp_path: Path
) -> None:
    """回填比每日更新晚跑：先載入的廢止在舊規則載入後補上停止適用。"""
    repeal = _pack(_rule("3", "115年10月1日", "廢止「甲要點」，並自即日生效。", "甲字第3號"))
    site = _gazette_site(repeal)
    sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert _ceased(conn) == []

    earlier = _pack(_rule("1", "114年1月2日", "訂定「甲要點」，並自即日生效。", "甲字第1號"))
    _add_pack(site, "114-01_01.zip", earlier)
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2025, 1, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert _ceased(conn) == [("甲字第1號", date(2026, 10, 1), "甲字第3號")]
    assert "repeal_unmatched" not in report.problems


def test_a_repeal_matching_rules_of_two_issuers_ceases_nothing(conn: Conn, tmp_path: Path) -> None:
    pack = _pack(
        _rule("1", "114年1月2日", "訂定「作業要點」，並自即日生效。", "甲字第1號"),
        _record(
            "2",
            "行政規則",
            "經濟部令：訂定「作業要點」",
            "乙字第1號",
            on="114年2月2日",
            subject="訂定「作業要點」，並自即日生效。",
            body="訂定「作業要點」。",
        ).replace("<![CDATA[環境部]]>", "<![CDATA[經濟部]]>"),
        _rule("3", "115年10月1日", "廢止「作業要點」，並自即日生效。", "甲字第3號"),
    )
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=_gazette_site(pack),
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert _ceased(conn) == []
    assert report.problems == {"repeal_ambiguous": 1}


# --- 函釋的下架：連續兩次完整走訪沒出現 -------------------------------------------------------


def _crawl_site(n: int = 30) -> FakeClient:
    """一個葉分類 `n` 件，全部是 108 年的函（語料有當時的第 7 條）。"""
    site = _moenv_site()
    leaf = moenv.LIST.format(feid="1702", page=1)
    site.pages[leaf] = "".join(
        _row(str(100 + i), f"環署水字第{1080000100 + i}號函", "108.05.01") for i in range(n)
    )
    for i in range(n):
        site.pages[moenv.DETAIL.format(id=str(100 + i))] = _detail(
            f"環署水字第{1080000100 + i}號", "民國 108 年 05 月 01 日", ["水污染防治法第7條"]
        )
    return site


def _hide(site: FakeClient, i: int) -> None:
    leaf = moenv.LIST.format(feid="1702", page=1)
    site.pages[leaf] = site.pages[leaf].replace(
        _row(str(100 + i), f"環署水字第{1080000100 + i}號函", "108.05.01"), ""
    )


def _crawl(conn: Conn, root: Path, site: FakeClient, **kw: Any) -> Report:
    return sync(
        conn, "moenv", root, client=site, policy=POLICY, sleep=_no_sleep, today=lambda: TODAY, **kw
    )


def test_a_letter_missing_from_two_complete_crawls_is_dropped_and_ceases(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _crawl_site()
    assert _crawl(conn, tmp_path, site).crawl_complete
    _hide(site, 0)
    first = _crawl(conn, tmp_path, site)
    assert (first.crawl_complete, first.dropped) == (True, 0)
    # 只看近期的那種同步不是全量走訪，不算數
    assert _crawl(conn, tmp_path, site, since=date(2026, 1, 1)).crawl_complete is None
    second = _crawl(conn, tmp_path, site)
    assert (second.dropped, second.cessations) == (1, 1)
    assert conn.execute(
        "SELECT key, dropped_on, missed_crawls FROM source.item WHERE dropped_on IS NOT NULL"
    ).fetchall() == [("環署水字第1080000100號", TODAY, 2)]
    assert _ceased(conn) == [("環署水字第1080000100號", TODAY, None)]
    assert conn.execute("SELECT cause::text, dropped_key FROM guidance.cessation").fetchall() == [
        ("taken_down", "環署水字第1080000100號")
    ]
    # 公文不刪除
    assert conn.execute("SELECT count(*) FROM guidance.document").fetchone() == (30,)

    # 又出現：記問題待人判斷，停止適用不撤回
    back = _crawl_site()
    again = _crawl(conn, tmp_path, back)
    assert again.problems["reappeared"] == 1
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert len(_ceased(conn)) == 1
    # 下架是終態：再出現也不歸零、不撤銷
    assert conn.execute(
        "SELECT missed_crawls, dropped_on FROM source.item WHERE dropped_on IS NOT NULL"
    ).fetchall() == [(2, TODAY)]


def test_a_crawl_missing_too_many_letters_infers_nothing(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _crawl_site()
    _crawl(conn, tmp_path, site)
    for i in range(5):
        _hide(site, i)
    for _ in range(2):
        report = _crawl(conn, tmp_path, site)
        assert (report.crawl_complete, report.dropped) == (False, 0)
    assert conn.execute(
        "SELECT max(missed_crawls) FROM source.item WHERE feed = 'moenv_guidance'"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("廢止「甲要點」，並自即日生效。", gazette.Actions((), (), ("甲要點",), None)),
        (
            "廢止「甲要點」及「乙基準」，並自中華民國一百十六年一月一日生效。",
            gazette.Actions((), (), ("甲要點", "乙基準"), date(2027, 1, 1)),
        ),
        ("停止適用「丁原則」。", gazette.Actions((), (), ("丁原則",), None)),
        # 只廢止其中幾點，規則還在
        ("廢止「甲要點」第五點，並自即日生效。", gazette.Actions((), (), (), None)),
        # 同時修正兩部、其中一部改名：只有改名的那一部連成同一條規則
        (
            "修正「甲要點」、「乙原則」第三點，名稱並修正為「丙原則」，自即日生效。",
            gazette.Actions(("甲要點", "乙原則", "丙原則"), (("乙原則", "丙原則"),), (), None),
        ),
    ],
)
def test_gazette_subject_actions(subject: str, expected: gazette.Actions) -> None:
    assert gazette.actions(subject) == expected


def test_a_rule_repealed_twice_ceases_at_the_first_repeal(conn: Conn, tmp_path: Path) -> None:
    pack = _pack(
        _rule("1", "114年1月2日", "訂定「甲要點」，並自即日生效。", "甲字第1號"),
        _rule("2", "114年3月2日", "訂定「乙要點」，並自即日生效。", "甲字第2號"),
        _rule("3", "115年1月5日", "廢止「甲要點」，並自即日生效。", "甲字第3號"),
        _rule("4", "115年10月1日", "廢止「甲要點」及「乙要點」，並自即日生效。", "甲字第4號"),
    )
    report = sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 1, 1),
        client=_gazette_site(pack),
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert _ceased(conn) == [
        ("甲字第1號", date(2026, 1, 5), "甲字第3號"),
        ("甲字第2號", date(2026, 10, 1), "甲字第4號"),
    ]
    assert report.problems == {}


def test_the_default_gazette_window_starts_on_the_first_of_last_month(
    conn: Conn, tmp_path: Path
) -> None:
    site = FakeClient({})
    sync(
        conn,
        "gazette",
        tmp_path,
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
        today=lambda: date(2026, 1, 15),
    )
    assert site.requested == [
        gazette.MONTH_LIST,
        gazette.YEAR_LIST.format(year=114),
        gazette.YEAR_LIST.format(year=115),
    ]
