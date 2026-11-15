"""生產者標籤改名（0069）：舊的專案名從 derivation 消失，其他生產者不動。"""

from pathlib import Path

from canon.migrate import apply_all
from tests.test_migrate import _at, _drop


def test_the_rule_producers_take_the_service_prefix(dsn: str, tmp_path: Path) -> None:
    name = "producer_rename"
    try:
        with _at(dsn, name, tmp_path, "0069") as conn:
            conn.execute(
                "INSERT INTO annotation.derivation (method, producer, producer_version) VALUES"
                " ('rule', 'envcite-delegation-rule', '1'),"
                " ('rule', 'envcite-guidance-sync', '1'),"
                " ('llm', 'some-model', '1')"
            )
            conn.commit()

            assert apply_all(conn) == ["0069_canon_producers.sql"]

            producers = sorted(
                str(p) for (p,) in conn.execute("SELECT producer FROM annotation.derivation")
            )
            assert producers == ["canon-delegation-rule", "canon-guidance-sync", "some-model"]
    finally:
        _drop(dsn, name)
