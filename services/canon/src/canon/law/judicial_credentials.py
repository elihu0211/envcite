"""司法院裁判書 API 的帳密。只從密鑰檔讀，不從環境變數讀。

叢集把封印過的密鑰掛在 `/var/run/canon/judicial.json`。該路徑沒有檔時，改讀服務目錄的
`judicial.local.json`（版控忽略）。兩處都是同一份 JSON：`user`、`password`。其他人不可讀
（本機 `0600`、叢集掛載 `0440` 都可以）；例外訊息不含帳號與密碼。
"""

import json
import stat
from dataclasses import dataclass
from pathlib import Path

#: 部署時由密鑰卷掛上的路徑。存在就用它。
SYSTEM_CREDENTIALS = Path("/var/run/canon/judicial.json")
#: 本機路徑：`<canon 服務目錄>/judicial.local.json`。
LOCAL_CREDENTIALS = Path(__file__).resolve().parents[3] / "judicial.local.json"


class CredentialsError(Exception):
    """密鑰檔不存在、其他人可讀或內容不符。訊息不含帳號與密碼。"""


@dataclass(frozen=True, slots=True)
class Credentials:
    """一組平臺帳號。"""

    user: str
    password: str


def credentials_path() -> Path:
    """叢集掛載優先，否則本機檔。不檢查內容。"""
    if SYSTEM_CREDENTIALS.is_file():
        return SYSTEM_CREDENTIALS
    return LOCAL_CREDENTIALS


def read_credentials(path: Path) -> Credentials:
    """讀一份密鑰檔。其他人可讀、所有者不可讀，或欄位空著，都拒絕。"""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as e:
        raise CredentialsError("密鑰檔讀不到") from e
    if mode & 0o007 or not mode & stat.S_IRUSR:
        raise CredentialsError("密鑰檔其他人可讀或所有者不可讀")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise CredentialsError("密鑰檔不是 JSON 物件") from e
    if not isinstance(payload, dict):
        raise CredentialsError("密鑰檔不是 JSON 物件")
    user, password = payload.get("user"), payload.get("password")
    if (
        not isinstance(user, str)
        or not user.strip()
        or not isinstance(password, str)
        or not password
    ):
        raise CredentialsError("密鑰檔缺 user 或 password")
    return Credentials(user.strip(), password)
