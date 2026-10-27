"""對模型端點（`models.toml` 的 `[endpoint.*]`）發請求時共用的標頭。

urllib 預設的 `Python-urllib/x.y` 會被端點前面的 WAF（如 Cloudflare 的 error 1010）擋下，
所以明確自報身分；不偽裝成瀏覽器或其他工具。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    USER_AGENT = f"envcite-ontology/{version('envcite-ontology')}"
except PackageNotFoundError:  # 未安裝的原始碼樹
    USER_AGENT = "envcite-ontology"


def json_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
