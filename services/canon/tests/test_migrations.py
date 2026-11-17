"""遷移檔的編號：連續、唯一；已套用的歷史重複不再增加。

兩條分支各自取了「下一個」號碼，合併後同一個編號就有兩個檔。檔名不同所以都套得上，但編號失去
順序意義：看檔名排序才知道誰先誰後，也看不出缺了哪一個。CI 在第二條分支 rebase 之後才看得到兩者
並存，所以守在這裡：衝突時把自己的檔改成下一個空號。
"""

import re
from collections import defaultdict

from canon.migrate import MIGRATIONS_DIR

_NAME = re.compile(r"^(\d{4})_[a-z][a-z0-9_]*\.sql$")

#: 已套用在各環境的重複編號：檔名記在 `schema_migration`，改名會被當成「已套用的檔不見了」而拒絕，
#: 所以只能留著。新的重複一律不收（編號衝突時改自己的檔）。
LEGACY_DUPLICATES = {
    "0002": {"0002_drop_concept_relation.sql", "0002_versioned_tokenizer.sql"},
    "0003": {"0003_suspension_enum.sql", "0003_suspension_status.sql"},
}


def _by_number() -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _NAME.match(path.name)
        assert match, f"遷移檔名要是 四位編號_小寫底線名稱.sql：{path.name}"
        found[match.group(1)].add(path.name)
    return found


def test_a_migration_number_is_taken_once() -> None:
    clashes = {
        number: sorted(names)
        for number, names in _by_number().items()
        if len(names) > 1 and names != LEGACY_DUPLICATES.get(number)
    }

    assert not clashes, (
        f"遷移編號重複：{clashes}。兩條分支取了同一個「下一個」號碼；rebase 到主線後，"
        "把自己的檔改成下一個空號（已合併的檔不能改名）"
    )


def test_the_numbers_have_no_gaps() -> None:
    numbers = sorted(int(n) for n in _by_number())

    assert numbers == list(range(1, len(numbers) + 1)), f"遷移編號不連續：{numbers}"
