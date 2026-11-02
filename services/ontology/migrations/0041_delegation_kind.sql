-- 0041_delegation_kind — 規範關係的第一種「授權」是一種主張：新增主張種類 delegation
--
-- 列舉值新增後要先提交，下一個檔案才能使用（migrate.py 逐檔提交），所以獨立成檔。

ALTER TYPE annotation.assertion_kind ADD VALUE 'delegation';
