-- 0068_canon_roles — 資料庫角色以服務名 canon 命名：把授權從舊角色整批搬到新角色
--
-- 不用 ALTER ROLE … RENAME：正式環境的擁有者沒有建立或改名角色的權限（見 0027），角色由部署庫的
-- CloudNativePG managed roles 建立，改名在那一側是「新建一組、移除舊的」。授權跟著角色走，所以
-- 新角色要得到舊角色的全部授權，舊角色要失去它們。
--
-- 授權從系統目錄讀（schema、資料表與序列、函式、預設權限），不重述 0027 以來的每一條 GRANT：
-- 搬完之後新角色的授權與舊角色搬走前逐項相同，tests/test_role_move.py 以 has_*_privilege 比對。
-- 舊角色遷移後是沒有任何授權的空角色，由部署庫移除。
--
-- 0027 到 0067 已套用、有檢查碼，沿用舊名，不改；本檔之後的授權一律寫新名。
-- 新角色同 0027：不存在才建立，建不起來（擁有者沒有建角色的權限）就先由部署庫建好。

DO $$
DECLARE
    pair     text[];
    old_name text;
    new_name text;
    old_oid  oid;
    g        record;
BEGIN
    FOREACH pair SLICE 1 IN ARRAY ARRAY[
        ['envcite_ontology_reader', 'canon_reader'],
        ['envcite_ontology_writer', 'canon_writer'],
        ['envcite_ontology_reviewer', 'canon_reviewer']
    ] LOOP
        old_name := pair[1];
        new_name := pair[2];
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = new_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', new_name);
        END IF;
        SELECT oid INTO old_oid FROM pg_roles WHERE rolname = old_name;
        CONTINUE WHEN old_oid IS NULL;

        FOR g IN
            SELECT format('SCHEMA %I', n.nspname) AS target, x.privilege_type AS priv, x.is_grantable AS opt
              FROM pg_namespace AS n, aclexplode(n.nspacl) AS x
             WHERE x.grantee = old_oid
            UNION ALL
            SELECT format('%s %s', CASE c.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, c.oid::regclass),
                   x.privilege_type, x.is_grantable
              FROM pg_class AS c, aclexplode(c.relacl) AS x
             WHERE x.grantee = old_oid
            UNION ALL
            SELECT format('%s %s', CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, p.oid::regprocedure),
                   x.privilege_type, x.is_grantable
              FROM pg_proc AS p, aclexplode(p.proacl) AS x
             WHERE x.grantee = old_oid
        LOOP
            EXECUTE format('GRANT %s ON %s TO %I%s', g.priv, g.target, new_name,
                           CASE WHEN g.opt THEN ' WITH GRANT OPTION' ELSE '' END);
            EXECUTE format('REVOKE %s ON %s FROM %I', g.priv, g.target, old_name);
        END LOOP;

        -- 預設權限：之後新建的資料表與序列自動授權給新角色
        FOR g IN
            SELECT d.defaclrole::regrole::text AS creator,
                   n.nspname AS schema_name,
                   CASE d.defaclobjtype WHEN 'r' THEN 'TABLES' WHEN 'S' THEN 'SEQUENCES'
                                        WHEN 'f' THEN 'FUNCTIONS' END AS kind,
                   x.privilege_type AS priv, x.is_grantable AS opt
              FROM pg_default_acl AS d
              LEFT JOIN pg_namespace AS n ON n.oid = d.defaclnamespace,
                   aclexplode(d.defaclacl) AS x
             WHERE x.grantee = old_oid AND d.defaclobjtype IN ('r', 'S', 'f')
        LOOP
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %s %s GRANT %s ON %s TO %I%s',
                           g.creator,
                           CASE WHEN g.schema_name IS NULL THEN '' ELSE format('IN SCHEMA %I', g.schema_name) END,
                           g.priv, g.kind, new_name,
                           CASE WHEN g.opt THEN ' WITH GRANT OPTION' ELSE '' END);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %s %s REVOKE %s ON %s FROM %I',
                           g.creator,
                           CASE WHEN g.schema_name IS NULL THEN '' ELSE format('IN SCHEMA %I', g.schema_name) END,
                           g.priv, g.kind, old_name);
        END LOOP;
    END LOOP;
END
$$;

-- 函式本體裡寫死了角色名，只能重定義
CREATE OR REPLACE FUNCTION annotation.loaded_settled_needs_reviewer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.confirm_state <> 'extracted'
       AND NOT pg_has_role(current_user, 'canon_reviewer', 'MEMBER') THEN
        RAISE EXCEPTION 'only a reviewer can load an assertion as %', NEW.confirm_state
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$function$;
