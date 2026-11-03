"""Acquiring administrative rules from the 行政院公報 open data and the 環境部 interpretation
pages: parsed from the real page shapes, written through the guidance loader one document per
transaction, anchored only to the text in force on the issue date, re-runnable."""

import io
import zipfile
from datetime import date
from pathlib import Path

import psycopg
import pytest

from envcite_jobkit import EndpointError, ExitCode, Policy
from envcite_ontology import guidance_sync
from envcite_ontology.acquire import gazette, moenv

Conn = psycopg.Connection[tuple[object, ...]]

POLICY = Policy(name="test", max_attempts=1, backoff_base_seconds=0.0)


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
    report = guidance_sync.sync(
        conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep
    )
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

    again = guidance_sync.sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    assert (again.listed, again.documents) == (0, 0)


def test_moenv_rescan_never_rewrites_a_loaded_letter(
    conn: Conn, sample: None, tmp_path: Path
) -> None:
    site = _moenv_site()
    guidance_sync.sync(conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep)
    derivations = conn.execute("SELECT count(*) FROM annotation.derivation").fetchone()

    site.pages[moenv.DETAIL.format(id="12")] = _detail(
        "環署水字第0990000002號", "民國 99 年 03 月 01 日", [], subject="網站改了轉錄"
    )
    report = guidance_sync.sync(
        conn, "moenv", tmp_path, rescan=True, client=site, policy=POLICY, sleep=_no_sleep
    )
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
    report = guidance_sync.sync(
        conn, "moenv", tmp_path, client=site, policy=POLICY, sleep=_no_sleep
    )
    assert (report.dead_lettered, report.documents) == (1, 1)
    assert report.exit_code == ExitCode.FAILED


# --- 行政院公報：XML 形狀取自實包（115-10_01.zip），內容縮短 ---------------------------------


def _record(meta: str, large: str, title: str, no: str = "環部水字第1150000001號") -> str:
    return f"""<Record>
<MetaId><![CDATA[{meta}]]></MetaId>
<Doc_Style_LName><![CDATA[{large}]]></Doc_Style_LName>
<PubGovName><![CDATA[環境部]]></PubGovName>
<Date_Created><![CDATA[中華民國115年10月1日]]></Date_Created>
<GazetteId><![CDATA[{no}]]></GazetteId>
<Title><![CDATA[{title}]]></Title>
<ThemeSubject><![CDATA[訂定「放流水標準適用原則」。]]></ThemeSubject>
<GazetteHTML><![CDATA[https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032182/ch04/Eg.htm]]></GazetteHTML>
<HTMLContent><![CDATA[<div class="information"><div class="announcement">
<span>中華民國115年10月1日</span></div></div>
<p class="line2_indent">訂定「放流水標準適用原則」，並自即日生效。</p>
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


def _gazette_site() -> FakeClient:
    link = (
        '<a href="fileView.do?fileType=zipPath&amp;fileName=abc&amp;attached'
        '&amp;zipfile=115-10-01.zip">115-10-01.zip</a>'
    )
    url = f"{gazette.BASE}fileView.do?fileType=zipPath&fileName=abc&attached&zipfile=115-10-01.zip"
    pack = _pack(
        _record("1", "行政規則", "環境部令：訂定「放流水標準適用原則」"),
        _record("2", "法規", "環境部令：修正「放流水標準」"),
        _record("3", "行政規則", "環境部令：訂定「水污染裁罰疑義處理原則」", no="環部水字第2號"),
    )
    return FakeClient({gazette.MONTH_LIST: link}, {url: pack})


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
    ("title", "kind"),
    [
        ("環境部令：訂定「放流水標準適用原則」", "interpretation"),
        ("環境部令：修正「違反水污染防治法罰鍰額度裁罰準則」", "discretion_standard"),
    ],
)
def test_gazette_rule_kind_follows_the_title(title: str, kind: str) -> None:
    assert gazette.rule_kind(title) == kind


def test_gazette_pack_name_bounds_the_range() -> None:
    assert gazette.Pack("115-10_02.zip", "u").covers_from(date(2026, 10, 20))
    assert not gazette.Pack("115-09_04.zip", "u").covers_from(date(2026, 10, 1))
    assert not gazette.Pack("115-10-01.zip", "u").covers_from(date(2026, 10, 2))


def test_gazette_sync_writes_rules_and_reports_what_it_could_not(
    conn: Conn, tmp_path: Path
) -> None:
    site = _gazette_site()
    report = guidance_sync.sync(
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

    again = guidance_sync.sync(
        conn,
        "gazette",
        tmp_path,
        since=date(2026, 10, 1),
        client=site,
        policy=POLICY,
        sleep=_no_sleep,
    )
    assert again.listed == 0
