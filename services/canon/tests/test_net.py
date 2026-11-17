"""`canon.net` 的對外身分：User-Agent 取自套件中繼資料，不在程式裡寫死。"""

import re

from canon.net import USER_AGENT


def test_user_agent_names_version_and_contact() -> None:
    """版本與聯絡處缺任一個都會讓取得站台無從得知找誰；缺 `[project.urls]` 時這裡會紅。"""
    assert re.fullmatch(r"canon/\d+\.\d+\.\d+ \(\+https://\S+\)", USER_AGENT)
