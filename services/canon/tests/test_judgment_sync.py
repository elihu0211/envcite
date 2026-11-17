"""Judicial API sync keeps a queue, drops non-administrative ids, and never treats transport
failures as a takedown."""

import json
import stat
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from canon.db import Conn
from canon.law.judgment_pdf import PdfRejected
from canon.law.judgment_sync import SyncResult, sync
from canon.law.judicial_api import (
    AUTH_URL,
    JLIST_URL,
    AuthRejected,
    Client,
    EndpointError,
)
from canon.law.judicial_credentials import CredentialsError, read_credentials
from tests.test_import import _scalar

TAIPEI = ZoneInfo("Asia/Taipei")
JID = "TPAA,110,上,1,20210101,1"


def _at(*hours: int) -> Callable[[], datetime]:
    moments = [datetime(2026, 11, 16, hour, tzinfo=TAIPEI) for hour in hours]
    index = 0

    def now() -> datetime:
        nonlocal index
        moment = moments[min(index, len(moments) - 1)]
        index += 1
        return moment

    return now


class _Api:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.changes: list[dict[str, object]] = []
        self.docs: dict[str, object] = {}
        self.fail: set[str] = set()
        self.auth: dict[str, str] = {"Token": "t"}
        self.reject_once: set[str] = set()

    def __call__(self, url: str, body: Mapping[str, str]) -> object:
        self.calls.append(url.rsplit("/", 1)[-1])
        if url == AUTH_URL:
            return self.auth
        if url == JLIST_URL:
            assert body["token"] == "t"
            return self.changes
        jid = body["j"]
        if jid in self.fail:
            raise EndpointError("JDoc")
        if jid in self.reject_once:
            self.reject_once.remove(jid)
            return {"error": "驗證失敗"}
        return self.docs[jid]


def _text(jid: str = JID, body: str = "本件適用噪音管制法第二條。") -> dict[str, object]:
    return {
        "JTITLE": "噪音管制法事件",
        "JDATE": "20210101",
        "JFULLX": {"JFULLTYPE": "text", "JFULLCONTENT": body},
        "JID": jid,
    }


def _sync(
    conn: Conn, api: _Api, *hours: int, files: Callable[[str], str | None] | None = None
) -> SyncResult:
    return sync(conn, Client("user", "secret", api), now=_at(*hours), pause=0, files=files)


def _pdf_doc(jid: str = JID) -> dict[str, object]:
    return {
        "JTITLE": "噪音管制法事件",
        "JDATE": "20210101",
        "JFULLX": {"JFULLTYPE": "file", "JFULLPDF": "https://data.judicial.gov.tw/a.pdf"},
        "JID": jid,
    }


def test_outside_the_window_nothing_is_called(conn: Conn) -> None:
    api = _Api()

    result = _sync(conn, api, 10)

    assert result.skipped and result.exit_code == 0
    assert api.calls == []


def test_an_administrative_text_is_stored_and_other_categories_are_not_asked(conn: Conn) -> None:
    api = _Api()
    criminal = "TPSM,110,訴,1,20210101,1"
    api.changes = [{"date": "2026-11-09", "list": [JID, criminal]}]
    api.docs[JID] = _text()

    result = _sync(conn, api, 1)

    assert (result.listed, result.stored, result.exit_code) == (1, 1, 0)
    assert _scalar(conn, "SELECT body FROM judgment.decision") == "本件適用噪音管制法第二條。"
    assert _scalar(conn, "SELECT status::text FROM judgment.fetch") == "stored"
    assert criminal not in "".join(api.calls)


def test_a_missing_document_is_deleted_and_a_file_is_not_downloaded_without_a_fetcher(
    conn: Conn,
) -> None:
    gone = "TPAA,110,上,2,20210101,1"
    filed = "KSBA,110,訴,3,20210101,1"
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [gone, filed]}]
    api.docs[gone] = {"error": "查無資料，本裁判可能未公開或已從系統移除"}
    api.docs[filed] = {"JFULLX": {"JFULLTYPE": "file", "JFULLPDF": "https://example.invalid/a.pdf"}}

    result = _sync(conn, api, 1)

    assert (result.taken_down, result.file_only) == (1, 1)
    assert _scalar(conn, "SELECT count(*) FROM judgment.decision") == 0
    assert _scalar(conn, "SELECT jid FROM judgment.takedown") == gone
    assert sorted(
        (str(status), str(jid))
        for status, jid in conn.execute("SELECT status::text, jid FROM judgment.fetch")
    ) == [("file_only", filed), ("taken_down", gone)]


def test_a_transport_failure_stays_pending_and_the_next_night_finishes_it_before_a_new_list(
    conn: Conn,
) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _text()
    api.fail.add(JID)

    first = _sync(conn, api, 1)

    assert first.stored == 0
    assert _scalar(conn, "SELECT last_error FROM judgment.fetch") == "transient"
    api.fail.clear()
    api.changes = []
    api.calls.clear()

    second = _sync(conn, api, 1)

    assert second.stored == 1
    assert api.calls.index("JDoc") < api.calls.index("JList")


def test_the_window_closing_leaves_the_rest_queued(conn: Conn) -> None:
    other = "TCBA,110,訴,4,20210101,1"
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID, other]}]
    api.docs[JID] = _text()
    api.docs[other] = _text(other)

    result = _sync(conn, api, 1, 1, 1, 6)

    assert result.stored == 1 and result.pending == 1 and result.exit_code == 0
    assert api.calls.count("JDoc") == 1


def test_an_unknown_administrative_court_is_not_fetched(conn: Conn) -> None:
    unknown = "ZZAA,110,訴,5,20210101,1"
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [unknown]}]

    _sync(conn, api, 1)

    assert "JDoc" not in api.calls
    assert _scalar(conn, "SELECT status::text FROM judgment.fetch") == "unmapped"


def test_the_same_jid_again_replaces_the_stored_text(conn: Conn) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _text()
    _sync(conn, api, 1)
    api.docs[JID] = _text(body="更正後的全文。")

    _sync(conn, api, 1)

    assert _scalar(conn, "SELECT count(*) FROM judgment.decision") == 1
    assert _scalar(conn, "SELECT body FROM judgment.decision") == "更正後的全文。"


def test_a_rejected_token_is_refreshed_once(conn: Conn) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _text()
    api.reject_once.add(JID)

    result = _sync(conn, api, 1)

    assert result.stored == 1
    assert api.calls.count("Auth") == 2


def test_bad_credentials_do_not_fetch_documents(conn: Conn) -> None:
    api = _Api()
    api.auth = {"error": "驗證失敗"}
    api.changes = [{"date": "2026-11-09", "list": [JID]}]

    result = _sync(conn, api, 1)

    assert result.exit_code == 2
    assert "JDoc" not in api.calls
    with pytest.raises(AuthRejected):
        Client("user", "secret", api).document(JID)


def test_credentials_must_be_unreadable_by_others_and_errors_omit_the_secret(
    tmp_path: Path,
) -> None:
    path = tmp_path / "judicial.json"
    path.write_text(json.dumps({"user": "member", "password": "s3cret-不能出現"}), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert read_credentials(path).user == "member"
    path.chmod(stat.S_IRUSR | stat.S_IRGRP)
    assert read_credentials(path).user == "member"
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
    with pytest.raises(CredentialsError, match="其他人可讀") as raised:
        read_credentials(path)
    assert "s3cret" not in str(raised.value)


def test_a_pdf_with_a_text_layer_is_stored_like_a_text(conn: Conn) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _pdf_doc()
    asked: list[str] = []

    def files(url: str) -> str | None:
        asked.append(url)
        return "本件適用噪音管制法第二條。"

    result = _sync(conn, api, 1, files=files)

    assert (result.stored, result.file_only) == (1, 0)
    assert asked == ["https://data.judicial.gov.tw/a.pdf"]
    assert _scalar(conn, "SELECT body FROM judgment.decision") == "本件適用噪音管制法第二條。"
    assert _scalar(conn, "SELECT status::text FROM judgment.fetch") == "stored"


@pytest.mark.parametrize("reason", ["untrusted_url", "too_large", "unreadable", "download_failed"])
def test_a_rejected_pdf_is_file_only_with_the_reason(conn: Conn, reason: str) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _pdf_doc()

    def files(url: str) -> str | None:
        raise PdfRejected(reason)

    result = _sync(conn, api, 1, files=files)

    assert (result.stored, result.file_only) == (0, 1)
    assert _scalar(conn, "SELECT last_error FROM judgment.fetch") == reason


def test_a_scanned_pdf_without_text_is_file_only(conn: Conn) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _pdf_doc()

    result = _sync(conn, api, 1, files=lambda _url: None)

    assert (result.stored, result.file_only) == (0, 1)
    assert _scalar(conn, "SELECT last_error FROM judgment.fetch") == "no_text_layer"


def test_a_download_failure_stays_pending_for_the_next_night(conn: Conn) -> None:
    api = _Api()
    api.changes = [{"date": "2026-11-09", "list": [JID]}]
    api.docs[JID] = _pdf_doc()

    def files(url: str) -> str | None:
        raise EndpointError("pdf")

    result = _sync(conn, api, 1, files=files)

    assert (result.stored, result.file_only, result.pending) == (0, 0, 1)
    assert _scalar(conn, "SELECT status::text FROM judgment.fetch") == "pending"
