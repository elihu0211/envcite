-- 0019_extraction_attempt — 抽取過的單元要留下紀錄，管線才收斂
--
-- 抽取沒有產出任何陳述（定義條文、模型判定沒有規範，或產出全被丟掉）時，標註表裡什麼也沒有，
-- 「這個單元這個文字讓這個抽取者抽過了」就無從得知，每次重跑都要再付一次模型費用。
-- 這張表只記「模型答過了」：錨點同陳述（單元，內容雜湊），文字改了雜湊變，自然回到待辦；
-- 模型沒答或答不成 JSON 不記，下次再試。

CREATE TABLE annotation.extraction_attempt (
    provision_id     bigint NOT NULL REFERENCES corpus.provision ON DELETE CASCADE,
    content_sha256   bytea NOT NULL,
    producer         text NOT NULL,
    producer_version text NOT NULL,
    statements       integer NOT NULL,
    dropped          integer NOT NULL,
    attempted_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provision_id, content_sha256, producer, producer_version),
    CHECK (octet_length(content_sha256) = 32),
    CHECK (producer <> '' AND producer_version <> ''),
    CHECK (statements >= 0 AND dropped >= 0)
);

COMMENT ON TABLE annotation.extraction_attempt IS
    '模型對某單元某文字答過一次（不論產出幾個陳述）；管線據此不重複抽取。';
COMMENT ON COLUMN annotation.extraction_attempt.statements IS '本次寫入的陳述數，可為 0。';
COMMENT ON COLUMN annotation.extraction_attempt.dropped IS '被丟掉的陳述數（引文不在原文、違反約束等）。';
