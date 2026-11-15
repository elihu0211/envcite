-- 0010_annotation — 受控詞彙與規範陳述的標註
--
-- 設計見 envcite-docs 02-04 §3、§6、§7，推論的用法見 02-05 §5。
--
-- 這一批只建推論用得到的最小子集：詞彙三張（陳述的行為槽與要件都指向概念）、推導活動、
-- 規範陳述與其行為槽、要件、但書、優先、罰則。條文性質、用語定義與適用例外（provision_role、
-- term_definition、applicability）等有生產者時再建。
--
-- 標註的錨點是（單元，內容雜湊），不是版本列編號（02-05 §5.6）。效力列更替而文字沒變時，
-- 版本列換新、雜湊不變，標註沿用；文字改了，雜湊變，舊標註自動不在該日的事實裡，須重新
-- 抽取與審核——不會靜默沿用到新文字。若把陳述綁在版本列編號上，每次效力區間改變，標註就
-- 無聲消失。

CREATE SCHEMA vocab;
CREATE SCHEMA annotation;

CREATE TYPE vocab.label_kind AS ENUM ('alt', 'hidden');
CREATE TYPE annotation.derivation_method AS ENUM ('rule', 'llm', 'human');
CREATE TYPE annotation.review_state AS ENUM ('extracted', 'confirmed', 'rejected');
CREATE TYPE annotation.statement_kind AS ENUM ('prescriptive', 'constitutive', 'penalty', 'reparation');
CREATE TYPE annotation.deontic AS ENUM ('obligation', 'prohibition', 'permission');
CREATE TYPE annotation.slot_role AS ENUM ('subject', 'action', 'object', 'medium', 'time', 'place');
-- scope：適用範圍（這條規範管不管得到這個事實）；duty：義務內容（合規與否的判準）；
-- limit：授權命令附表的限值。
CREATE TYPE annotation.cond_role AS ENUM ('scope', 'duty', 'limit');
CREATE TYPE annotation.comparator AS ENUM ('le', 'lt', 'ge', 'gt', 'eq', 'ne');
CREATE TYPE annotation.override_basis AS ENUM ('hierarchy', 'lex_specialis', 'lex_posterior', 'exception');
CREATE TYPE annotation.legal_effect AS ENUM ('fine', 'confiscation', 'restriction', 'deprivation', 'reputation', 'warning');

-- 受控詞彙（SKOS 的子集）。
CREATE TABLE vocab.concept_scheme (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code  text NOT NULL UNIQUE,
    title text NOT NULL,
    CHECK (code <> '' AND title <> '')
);

CREATE TABLE vocab.concept (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scheme_id  bigint NOT NULL REFERENCES vocab.concept_scheme,
    code       text NOT NULL,
    pref_label text NOT NULL,
    broader_id bigint,
    UNIQUE (scheme_id, code),
    -- 上位概念必屬同一個詞彙方案。
    UNIQUE (id, scheme_id),
    FOREIGN KEY (broader_id, scheme_id) REFERENCES vocab.concept (id, scheme_id),
    CHECK (code <> '' AND pref_label <> '')
);

CREATE TABLE vocab.concept_label (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept_id bigint NOT NULL REFERENCES vocab.concept ON DELETE CASCADE,
    label      text NOT NULL,
    kind       vocab.label_kind NOT NULL,
    UNIQUE (concept_id, label, kind),
    CHECK (label <> '')
);

-- 推導活動（PROV-O 的 Activity 與 Agent）：標註與抽取的關係各列都指向它。
CREATE TABLE annotation.derivation (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method        annotation.derivation_method NOT NULL,
    agent         text NOT NULL,
    agent_version text NOT NULL,
    run_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (agent <> '' AND agent_version <> '')
);

-- 規範陳述：推論的單位；一條文可含數個陳述。
CREATE TABLE annotation.statement (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provision_id   bigint NOT NULL REFERENCES corpus.provision ON DELETE CASCADE,
    content_sha256 bytea NOT NULL,
    span           int4range,
    kind           annotation.statement_kind NOT NULL,
    deontic        annotation.deontic,
    derivation_id  bigint NOT NULL REFERENCES annotation.derivation,
    review         annotation.review_state NOT NULL DEFAULT 'extracted',
    CHECK (octet_length(content_sha256) = 32),
    -- 義務方向只屬規範性陳述：但書本身是構成性的，不依種類分流就會被當成義務而導出違規。
    CHECK ((kind = 'prescriptive') = (deontic IS NOT NULL))
);

CREATE TABLE annotation.statement_slot (
    statement_id bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    slot         annotation.slot_role NOT NULL,
    concept_id   bigint REFERENCES vocab.concept,
    literal      text,
    PRIMARY KEY (statement_id, slot),
    CHECK (concept_id IS NOT NULL OR literal IS NOT NULL)
);

-- 要件採析取範式：同一 clause 內為「且」，不同 clause 之間為「或」，negated 為「非」。
-- 門檻值恰有一個來源：字面值、受控值，或委由授權命令（delegate_provision_id，查詢時取
-- 授權命令當日有效的限值）。
CREATE TABLE annotation.statement_condition (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    statement_id         bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    role                 annotation.cond_role NOT NULL,
    clause               smallint NOT NULL,
    concept_id           bigint NOT NULL REFERENCES vocab.concept,
    comparator           annotation.comparator NOT NULL,
    threshold            numeric(18,3),
    unit                 text,
    value_concept_id     bigint REFERENCES vocab.concept,
    negated              boolean NOT NULL DEFAULT false,
    -- 延後檢查：刪除一部法規時，指向它的單元與持有委任要件的陳述若同被連鎖刪除，
    -- 即時檢查會在連鎖刪除完成前就看到仍被引用。仍被其他法規的委任要件引用者，
    -- 到交易結束時依然擋下。
    delegate_provision_id bigint REFERENCES corpus.provision DEFERRABLE INITIALLY DEFERRED,
    CHECK (num_nonnulls(threshold, delegate_provision_id, value_concept_id) = 1),
    CHECK (role <> 'limit' OR threshold IS NOT NULL)
);

CREATE TABLE annotation.statement_exception (
    exception_id bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    excluded_id  bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    span         int4range,
    PRIMARY KEY (exception_id, excluded_id),
    CHECK (exception_id <> excluded_id)
);

CREATE TABLE annotation.statement_override (
    winner_id bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    loser_id  bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    basis     annotation.override_basis NOT NULL,
    PRIMARY KEY (winner_id, loser_id),
    CHECK (winner_id <> loser_id)
);

CREATE TABLE annotation.sanction (
    statement_id bigint PRIMARY KEY REFERENCES annotation.statement ON DELETE CASCADE,
    punishes_id  bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    effect       annotation.legal_effect NOT NULL,
    amount_text  text,
    min_amount   numeric(18,0),
    max_amount   numeric(18,0),
    CHECK (min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount),
    CHECK (statement_id <> punishes_id)
);

-- 錨點查詢（statements_at、threshold_at）與外鍵的連鎖刪除、反向查詢都走這些索引。
CREATE INDEX statement_by_anchor ON annotation.statement (provision_id, content_sha256);
CREATE INDEX statement_condition_by_statement ON annotation.statement_condition (statement_id);
CREATE INDEX statement_exception_by_excluded ON annotation.statement_exception (excluded_id);
CREATE INDEX statement_override_by_loser ON annotation.statement_override (loser_id);
CREATE INDEX sanction_by_punished ON annotation.sanction (punishes_id);

-- 某日有效單元上的陳述：取該日現行列的雜湊，與陳述錨定的雜湊相同者。只取已確認的，或
-- 由規則推導的；被拒絕的不取。
CREATE FUNCTION annotation.statements_at(p_resource bigint, p_language text, p_on date)
RETURNS SETOF annotation.statement
LANGUAGE sql STABLE
AS $$
    SELECT s.*
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS s ON s.provision_id = v.provision_id
                                    AND s.content_sha256 = v.content_sha256
      JOIN annotation.derivation AS d ON d.id = s.derivation_id
     WHERE a.status = 'in_force'
       AND s.review <> 'rejected'
       AND (s.review = 'confirmed' OR d.method = 'rule')
$$;

-- 要件的門檻值：字面值，或委由授權命令者取授權命令當日有效、已確認的限值陳述上的限值。
-- 限值不嵌在授權它的法律上：標準每修正一次，不必改動法律的標註。解析不到則為空值。
CREATE FUNCTION annotation.threshold_at(p_condition bigint, p_language text, p_on date)
RETURNS numeric
LANGUAGE sql STABLE
AS $$
    SELECT coalesce(c.threshold, (
        SELECT lim.threshold
          FROM corpus.provision AS dp
          JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
               ON a.provision_id = dp.id AND a.status = 'in_force'
          JOIN corpus.provision_version AS v ON v.id = a.version_id
          JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                         AND ls.content_sha256 = v.content_sha256
                                         AND ls.review = 'confirmed'
          JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                    AND lim.role = 'limit'
                                                    AND lim.concept_id = c.concept_id
         WHERE dp.id = c.delegate_provision_id))
      FROM annotation.statement_condition AS c
     WHERE c.id = p_condition
$$;
