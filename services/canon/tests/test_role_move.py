"""角色改名（0068）：新角色得到舊角色全部授權，舊角色一項不留，寫死角色名的函式跟著改。"""

from pathlib import Path

import psycopg

from canon.migrate import apply_all
from tests.test_migrate import _at, _drop

Conn = psycopg.Connection[tuple[object, ...]]

ROLES = {
    "envcite_ontology_reader": "canon_reader",
    "envcite_ontology_writer": "canon_writer",
    "envcite_ontology_reviewer": "canon_reviewer",
}
BASELINE = "role_move_baseline"

_USER_OBJECT = "!~ '^pg_|^information_schema$'"

# 以 has_*_privilege 逐項問資料庫，不讀 ACL 欄位：驗證搬移的結果，而不是重做搬移的做法。
_PRIVILEGES = f"""
SELECT 'schema', n.nspname::text, p
  FROM pg_namespace AS n, unnest(ARRAY['USAGE', 'CREATE']) AS p
 WHERE n.nspname::text {_USER_OBJECT} AND has_schema_privilege(%(r)s, n.oid, p)
UNION ALL
SELECT 'table', c.oid::regclass::text, p
  FROM pg_class AS c,
       unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES',
                    'TRIGGER']) AS p
 WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND c.relnamespace::regnamespace::text {_USER_OBJECT}
   AND has_table_privilege(%(r)s, c.oid, p)
UNION ALL
SELECT 'sequence', c.oid::regclass::text, p
  FROM pg_class AS c, unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS p
 WHERE c.relkind = 'S' AND c.relnamespace::regnamespace::text {_USER_OBJECT}
   AND has_sequence_privilege(%(r)s, c.oid, p)
UNION ALL
SELECT 'function', f.oid::regprocedure::text, 'EXECUTE'
  FROM pg_proc AS f
 WHERE f.pronamespace::regnamespace::text {_USER_OBJECT}
   AND has_function_privilege(%(r)s, f.oid, 'EXECUTE')
"""

_DEFAULTS = """
SELECT d.defaclrole::regrole::text, coalesce(d.defaclnamespace::regnamespace::text, ''),
       d.defaclobjtype::text, x.privilege_type
  FROM pg_default_acl AS d, aclexplode(d.defaclacl) AS x
 WHERE x.grantee = %(r)s::regrole
"""


def _held(conn: Conn, role: str) -> set[tuple[object, ...]]:
    return set(conn.execute(_PRIVILEGES, {"r": role}).fetchall())


def _defaults(conn: Conn, role: str) -> set[tuple[object, ...]]:
    return set(conn.execute(_DEFAULTS, {"r": role}).fetchall())


def test_the_new_roles_hold_what_the_old_ones_held_and_the_old_ones_hold_nothing(
    dsn: str, tmp_path: Path
) -> None:
    name = "role_move"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP ROLE IF EXISTS {BASELINE}")
        admin.execute(f"CREATE ROLE {BASELINE} NOLOGIN")
    try:
        with _at(dsn, name, tmp_path, "0068") as conn:
            nothing = _held(conn, BASELINE)
            before = {old: _held(conn, old) for old in ROLES}
            before_defaults = {old: _defaults(conn, old) for old in ROLES}
            for old, new in ROLES.items():
                assert len(before[old] - nothing) > 20, old
                assert before_defaults[old], old
                assert _held(conn, new) == nothing, new
                assert _defaults(conn, new) == set(), new

            assert apply_all(conn)[0] == "0068_canon_roles.sql"

            for old, new in ROLES.items():
                assert _held(conn, new) == before[old], new
                assert _held(conn, old) == nothing, old
                assert _defaults(conn, new) == before_defaults[old], new
                assert _defaults(conn, old) == set(), old
    finally:
        _drop(dsn, name)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {BASELINE}")


def test_the_reviewer_check_names_the_new_role(conn: Conn) -> None:
    (body,) = conn.execute(
        "SELECT prosrc FROM pg_proc WHERE proname = 'loaded_settled_needs_reviewer'"
    ).fetchone() or ("",)
    assert "'canon_reviewer'" in str(body)
    assert "envcite_ontology" not in str(body)
