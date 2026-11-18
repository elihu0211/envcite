"""每晚取司法院裁判書異動：先消化佇列，再要新清單，只收行政類全文。

服務時段是臺灣時間 0 時至 6 時。窗外出門不呼叫。JList 的非行政類 jid 不入佇列。
未知的行政法院代碼記 `unmapped`，不重試。`查無資料` 才撤下。全文是 PDF 時下載並抽文字層，
與 HTML 全文同一條入庫路徑；沒有文字層（掃描檔）、網址不可信或檔案不合，才記 `file_only` 與原因。
同一 jid 再出現改回 `pending`，因為內容可能已改。

審級代碼取自司法陽光網公開的法院表：TPA 最高、TPB 臺北高等、TCB 臺中高等、KSB 高雄高等。
112 年起的地方行政訴訟庭（地方行政法院）：TPT 臺北、TCT 臺中、KST 高雄，由司法院裁判書網址的檔名
（FILES/TPTA/…）與判決抬頭「高等行政法院地方行政訴訟庭」對照而得；其餘對不上的不猜。
"""

import argparse
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import NamedTuple
from zoneinfo import ZoneInfo

import psycopg

from canon.acquire.web import Http
from canon.clock import taipei_now
from canon.db import Conn, fetch, scalar
from canon.exit_codes import JobStopped, Retryable, fail
from canon.law import judgment, judgment_anchor
from canon.law.judgment_pdf import FileText, PdfRejected, fetcher
from canon.law.judicial_api import Client, EndpointError, removed
from canon.law.judicial_credentials import CredentialsError, credentials_path, read_credentials
from canon.rules import Rule
from canon.settings import DatabaseSettings, ModelsSettings
from canon.settings import load as load_settings
from canon.verification.config import ocr_from_settings
from envcite_jobkit import ExitCode

log = logging.getLogger(__name__)

PAUSE_SECONDS = 1.0
#: 連續這麼多筆暫時性失敗就停止本次：端點整夜不通時，逐筆等逾時會耗掉整個時段（每筆 60 秒），
#: 且 Job 的重試也各燒一遍；停下來由下一次重試或排程再試，待取列保持 `pending`。
TRANSIENT_STREAK_LIMIT = 5
#: 一頁 OCR 的逾時；視覺語言模型讀一頁約數秒到十數秒。
OCR_TIMEOUT_SECONDS = 180.0
_TAIPEI = ZoneInfo("Asia/Taipei")
_WINDOW_END = time(6, 0)
#: 載入的裁判標的推導來源；版本跟著引用抽取規則走，改規則時標示隨之改變。
RULE = Rule("canon-judicial-sync", judgment_anchor.RULE_VERSION)

#: 法院代碼（jid 第一欄去掉末位類別碼）→ 審級。
COURTS: Mapping[str, str] = {
    "TPA": "supreme",
    "TPB": "high",
    "TCB": "high",
    "KSB": "high",
    "TPT": "district",
    "TCT": "district",
    "KST": "district",
}

type Clock = Callable[[], datetime]
type Sleep = Callable[[float], None]


class _JidRow(NamedTuple):
    jid: str


class Stalled(Retryable):
    """連續暫時性失敗到上限。`counts` 是停下前累計的（入庫、撤下、僅檔案）。"""

    def __init__(self, counts: tuple[int, int, int]) -> None:
        super().__init__(f"連續 {TRANSIENT_STREAK_LIMIT} 筆暫時性失敗，停止本次（原因見上列警告）")
        self.counts = counts


@dataclass(frozen=True, slots=True)
class SyncResult:
    """一次 `canon judgment sync` 的計數。`exit_code` 0 含窗外與窗內沒跑完。"""

    skipped: bool = False
    listed: int = 0
    stored: int = 0
    taken_down: int = 0
    file_only: int = 0
    pending: int = 0
    exit_code: int = 0


def in_window(moment: datetime) -> bool:
    """臺灣時間 0 時起、6 時前。"""
    local = moment.astimezone(_TAIPEI)
    return local.time() < _WINDOW_END


def sync(
    conn: Conn,
    client: Client,
    *,
    now: Clock,
    sleep: Sleep = lambda _seconds: None,
    pause: float = PAUSE_SECONDS,
    files: FileText | None = None,
) -> SyncResult:
    """取完佇列裡的待取 jid，窗還開著才要新清單。整次不把密鑰寫進結果。

    `files` 取 PDF 全文；沒給就不下載，全文是檔案的裁判記 `file_only`。
    """
    if not in_window(now()):
        log.info("裁判書同步：不在服務時段")
        return SyncResult(skipped=True)
    stored = taken = filed = listed = 0
    try:
        _requeue_mapped(conn)
        stored, taken, filed = _drain(
            conn, client, now, sleep, pause, files, (stored, taken, filed)
        )
        if not in_window(now()):
            return _result(conn, stored, taken, filed)
        days = _changes(client.changes())
        listed = _enqueue(conn, days)
        stored, taken, filed = _drain(
            conn, client, now, sleep, pause, files, (stored, taken, filed)
        )
    except JobStopped as e:
        if isinstance(e, Stalled):
            stored, taken, filed = e.counts
        code = fail(conn, e, "judgment sync")
        return _result(conn, stored, taken, filed, code)
    return _result(conn, stored, taken, filed, listed=listed)


def _requeue_mapped(conn: Conn) -> None:
    """過去因法院代碼不在表上記 `unmapped` 的，表補上後改回待取；仍對不上的不動。"""
    rows = fetch(
        conn,
        "SELECT jid FROM judgment.fetch WHERE status = 'unmapped' AND last_error = 'unknown_court'",
        None,
        _JidRow,
    )
    for row in rows:
        if _court(row.jid) is not None:
            conn.execute(
                "UPDATE judgment.fetch SET status = 'pending', last_error = NULL,"
                " updated_on = CURRENT_DATE WHERE jid = %s",
                (row.jid,),
            )
    conn.commit()


def _drain(
    conn: Conn,
    client: Client,
    now: Clock,
    sleep: Sleep,
    pause: float,
    files: FileText | None,
    counts: tuple[int, int, int],
) -> tuple[int, int, int]:
    pending = [
        row.jid
        for row in fetch(
            conn,
            "SELECT jid FROM judgment.fetch WHERE status = 'pending' ORDER BY listed_on, jid",
            None,
            _JidRow,
        )
    ]
    stored, taken, filed = counts
    streak = 0
    for index, jid in enumerate(pending):
        if not in_window(now()):
            break
        if index:
            sleep(pause)
        outcome = _one(conn, client, jid, now, files)
        stored += outcome == "stored"
        taken += outcome == "taken_down"
        filed += outcome == "file_only"
        streak = streak + 1 if outcome == "transient" else 0
        if streak >= TRANSIENT_STREAK_LIMIT:
            raise Stalled((stored, taken, filed))
    return stored, taken, filed


def _one(conn: Conn, client: Client, jid: str, now: Clock, files: FileText | None) -> str:
    """處理一筆。回傳新狀態；留下 pending 的不計入完成數。"""
    if _court(jid) is None:
        _mark(conn, jid, "unmapped", "unknown_court")
        return "unmapped"
    try:
        payload = client.document(jid)
    except EndpointError as e:
        log.warning("裁判 %s 暫時性失敗：%s", jid, e)
        _mark(conn, jid, "pending", "transient")
        return "transient"
    if removed(payload):
        judgment.take_down(conn, [jid], now().astimezone(_TAIPEI).date())
        _mark(conn, jid, "taken_down", None)
        return "taken_down"
    built = _decision(jid, payload)
    if built == "file":
        built = _from_file(jid, payload, files)
    if built == "malformed":
        log.warning("裁判 %s 的 JDoc 形狀不合：%s", jid, _shape(payload))
    if isinstance(built, str):
        status, error = _unfinished(built)
        _mark(conn, jid, status, error)
        return "transient" if error == "transient" else status
    decided_on, document = built
    body = document["body"]
    assert isinstance(body, str)
    found = judgment_anchor.extract(conn, body, decided_on)
    document["cites"] = list(found.cites)
    try:
        judgment.load(
            conn,
            {
                "derivation": RULE.derivation,
                "decisions": [document],
            },
        )
    except judgment.JudgmentError:
        log.info("裁判 %s 未入庫", jid)
        _mark(conn, jid, "pending", "rejected")
        return "pending"
    _mark(conn, jid, "stored", None)
    return "stored"


#: 全文是檔案、記 `file_only` 的原因；`file` 是沒給下載函式，不記原因。
_FILE_REASONS = frozenset(
    {
        "no_text_layer",
        "untrusted_url",
        "too_large",
        "too_many_pages",
        "unreadable",
        "download_failed",
        "ocr_incomplete",
    }
)


def _unfinished(problem: str) -> tuple[str, str | None]:
    """未入庫的問題代碼 → （狀態，原因）。"""
    if problem == "unknown_court":
        return "unmapped", problem
    if problem == "file":
        return "file_only", None
    if problem in _FILE_REASONS:
        return "file_only", problem
    return "pending", problem


def _from_file(
    jid: str, payload: object, files: FileText | None
) -> tuple[date, dict[str, object]] | str:
    """全文是 PDF：下載抽字，當成文字全文走同一條路。對不上時回問題代碼。"""
    full = payload.get("JFULLX") if isinstance(payload, dict) else None
    url = full.get("JFULLPDF") if isinstance(full, dict) else None
    if files is None or not isinstance(url, str) or not isinstance(payload, dict):
        return "file"
    try:
        text = files(url)
    except PdfRejected as e:
        return str(e)
    except EndpointError as e:
        log.warning("裁判 %s 的 PDF 暫時性失敗：%s", jid, e)
        return "transient"
    if text is None:
        return "no_text_layer"
    return _decision(jid, {**payload, "JFULLX": {"JFULLTYPE": "text", "JFULLCONTENT": text}})


def _shape(payload: object) -> str:
    """JDoc 回應的鍵與 JFULLX 的型別，不含任何內容；供查 `malformed` 的原因。"""
    if not isinstance(payload, dict):
        return type(payload).__name__
    full = payload.get("JFULLX")
    inner = sorted(full) if isinstance(full, dict) else type(full).__name__
    return f"keys={sorted(payload)} JFULLX={inner} JTITLE={type(payload.get('JTITLE')).__name__}"


def _decision(jid: str, payload: object) -> tuple[date, dict[str, object]] | str:
    """JDoc → 載入用的裁判；對不上時回問題代碼或 `file`。"""
    if not isinstance(payload, dict):
        return "malformed"
    court = _court(jid)
    if court is None:
        return "unknown_court"
    full = payload.get("JFULLX")
    if not isinstance(full, dict):
        return "malformed"
    kind = full.get("JFULLTYPE")
    if kind == "file":
        return "file"
    text = full.get("JFULLCONTENT")
    if kind != "text" or not isinstance(text, str) or not text.strip():
        return "empty_body"
    decided = _decided(jid, payload.get("JDATE"))
    if decided is None:
        return "date_mismatch"
    title = payload.get("JTITLE")
    if not isinstance(title, str) or not title.strip():
        return "malformed"
    case_word = jid.split(",")[2]
    return decided, {
        "jid": jid,
        "decided_on": decided.isoformat(),
        "instance": court,
        "kind": "grand_chamber" if court == "supreme" and case_word == "大" else "ordinary",
        "title": title.strip(),
        "body": text,
    }


def _court(jid: str) -> str | None:
    court_code = jid.split(",")[0]
    if len(court_code) < 2 or not court_code.endswith("A"):
        return None
    return COURTS.get(court_code[:-1])


def _decided(jid: str, given: object) -> date | None:
    """jid 第 5 欄是裁判日。JDATE 有值時必須是同一天。"""
    parts = jid.split(",")
    if len(parts) < 5:
        return None
    parsed = _yyyymmdd(parts[4])
    if parsed is None:
        return None
    if given in (None, ""):
        return parsed
    if not isinstance(given, str):
        return None
    return parsed if _yyyymmdd(given) == parsed else None


def _yyyymmdd(value: str) -> date | None:
    digits = value.replace("-", "").replace("/", "")
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return None


def _changes(payload: object) -> list[tuple[date, list[str]]]:
    if not isinstance(payload, list):
        raise EndpointError("JList")
    days: list[tuple[date, list[str]]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise EndpointError("JList")
        listed, jids = item.get("date"), item.get("list")
        if not isinstance(listed, str) or not isinstance(jids, list):
            raise EndpointError("JList")
        try:
            on = date.fromisoformat(listed)
        except ValueError:
            raise EndpointError("JList") from None
        if not all(isinstance(jid, str) and jid.strip() for jid in jids):
            raise EndpointError("JList")
        days.append((on, [jid.strip() for jid in jids]))
    return days


def _enqueue(conn: Conn, days: list[tuple[date, list[str]]]) -> int:
    """行政類 jid 入佇列（再出現的改回待取）。回傳入列的筆數，含已在佇列者。"""
    count = 0
    for listed_on, jids in days:
        for jid in jids:
            if _court(jid) is None and not _administrative_but_unmapped(jid):
                continue
            count += 1
            conn.execute(
                "INSERT INTO judgment.fetch (jid, listed_on, status) VALUES (%s, %s, 'pending')"
                " ON CONFLICT (jid) DO UPDATE SET listed_on = EXCLUDED.listed_on,"
                " status = 'pending', last_error = NULL, updated_on = CURRENT_DATE",
                (jid, listed_on),
            )
    conn.commit()
    return count


def _administrative_but_unmapped(jid: str) -> bool:
    """第一欄末位是 A、但法院代碼不在表上：仍入列，取的時候再標 unmapped。"""
    code = jid.split(",")[0]
    return len(code) > 1 and code.endswith("A") and code[:-1].isalpha() and _court(jid) is None


def _mark(conn: Conn, jid: str, status: str, error: str | None) -> None:
    conn.execute(
        "UPDATE judgment.fetch SET status = %s, last_error = %s, updated_on = CURRENT_DATE"
        " WHERE jid = %s",
        (status, error, jid),
    )
    conn.commit()


def _result(
    conn: Conn,
    stored: int,
    taken: int,
    files: int,
    exit_code: int = 0,
    *,
    listed: int = 0,
) -> SyncResult:
    left = scalar(conn, "SELECT count(*) FROM judgment.fetch WHERE status = 'pending'", None, int)
    result = SyncResult(False, listed, stored, taken, files, left, exit_code)
    log.info("裁判書同步：%s", result)
    return result


def main() -> None:
    """`canon judgment sync`：讀密鑰檔，在服務時段取異動。"""
    parser = argparse.ArgumentParser(prog="canon judgment")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="取裁判書異動清單與全文（每日 0 時至 6 時）")
    parser.parse_args()
    try:
        creds = read_credentials(credentials_path())
    except CredentialsError as e:
        log.error("裁判書密鑰：%s", e)
        raise SystemExit(ExitCode.FATAL) from e
    settings = load_settings(DatabaseSettings)
    client = Client(creds.user, creds.password)
    http = Http(PAUSE_SECONDS)
    ocr = ocr_from_settings(load_settings(ModelsSettings), timeout=OCR_TIMEOUT_SECONDS)
    try:
        with psycopg.connect(settings.dsn) as conn:
            result = sync(conn, client, now=taipei_now, files=fetcher(http, ocr))
            raise SystemExit(result.exit_code)
    except psycopg.Error as e:
        log.error("裁判書同步中斷：資料庫")
        raise SystemExit(ExitCode.FAILED) from e
    finally:
        http.close()
