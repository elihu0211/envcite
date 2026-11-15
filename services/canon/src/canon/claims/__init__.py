"""標註文件的寫入。

人寫的 JSON 由 `annotate` 這個入口讀檔後交給這裡；抽取器與罰則連結也呼叫 `load`。
只寫入與錨定，
不接受：陳述是否成為推論事實，由 `verification` 的閘門與政策決定。

**錨點是文字，不是版本列。** 陳述附原文 `text`，載入時算雜湊，且該單元現行某版本列
須有同一雜湊，否則拒絕。效力列更替而文字沒變則沿用；文字改了則自動退出該日的事實
（`annotation.statements_at`），須重新標註。

**載入不接受任何陳述。** 狀態取文件的 `review`（預設 `extracted`）；下游只取 `confirmed`
（或規則推導）者。

**罰則只處罰已確認的陳述**（綱要強制）：所處罰的陳述在文件裡未確認、在資料庫未確認，或沒有指明
（處罰的是原文引用的別條）的罰則，不寫成陳述，存成待連結罰則（`annotation.held_penalty`），連同以它為
前提或為例外者；等義務確認後由 `verification.link` 寫成陳述。

一次載入是一個推導活動、一個交易：任一處不合，整份不寫入。每個主張寫完內容即封存
（`annotation.seal`）：已有內容相同、未駁回的主張時沿用既有的，所以同一份載入兩次不會多出主張。
修正時先以 `annotation.set_confirm_state` 把舊陳述駁回，再載入新的。

文件格式：

    {"derivation": {"method": "llm", "agent": "…", "agent_version": "…"},
     "review": "confirmed",
     "scheme": {"code": "env", "title": "…", "law": "<pcode，選填：方案只及於這部法規>"},
     "concepts": [{"code": "cod", "label": "…", "aliases": ["…"], "matches": {"cas": "…"}}],
     "definitions": [{"id": "d1", "law": "<pcode>", "path": "art_2", "text": "<原文>",
                      "term": "<code>", "definition": "…", "members": ["<code>"]}],
     "statements": [
       {"id": "s1", "law": "<pcode>", "path": "art_7/para_1", "text": "<原文>",
        "kind": "prescriptive", "deontic": "obligation",
        "slots": {"action": {"concept": "discharging"},
                  "subject": [{"literal": "事業", "liability": "act"},
                              {"literal": "負責人"}]},  # 多個值彼此擇一
        "conditions": [{"role": "duty", "clause": 1, "concept": "cod", "comparator": "le",
                        "threshold": 100, "unit": "mg/L", "negated": false, "judgement": false,
                        "delegate": {"law": "<pcode>", "path": "art_2"}, "permit": "<code>",
                        "method": "<code>", "averaging": "<code>"}],
        "exception_of": "s0",
        "sanction": {"punishes": "s1", "effect": "fine", "min": 60000, "max": 20000000,
                     "repeating": false, "discretionary": false,
                     "triggers": [{"statement": "s2", "kind": "order_lapsed"}]}}],
     "overrides": [{"winner": "s2", "loser": "s1", "basis": "lex_specialis"}]}

`conditions` 的門檻值恰給一個：`threshold`、`delegate`（委由授權命令的單元）、`value_concept`，
或 `permit`（委由個別處分：許可種類的概念，量值由呼叫端隨案件事實提供）。限值（`role: limit`）
可另標認定方式：`method`（檢測方法）與 `averaging`（平均時段），都是概念代碼。
主體槽位可標責任基礎 `liability`（`act`、`state`、`representative`），其他槽位不可。
罰則 `effect` 除處罰種類外有 `order`（改善命令等非處罰的行政命令）與 `disgorgement`（不法利得的
追繳）；`repeating` 標按次處罰；`discretionary` 標條文寫「得」的效果裁量；`triggers` 是它的前提：
另一個罰則（文件裡的 `statement`，或資料庫的 `statement_id`），`kind` 為 `order_lapsed`（命令屆期
未履行，前提須是 `order`）或 `sanction_imposed`（另一處分已作成），數個前提彼此是「或」。
概念的 `matches` 是外部對映（外部體系 → 識別碼），只記等同。
`judgement` 標要件中的不確定法律概念（情節重大、必要時）：系統不判定，呼叫端給的值視為其判斷，
未給則列為需判斷；限值不得標記。效果面裁量不是要件，用罰則的 `discretionary`。

模組：`loader`（入口與交易）、`statements`／`sanctions`／`links`（陳述、罰則與兩者之間的關係）、
`definitions`、`vocabulary`（詞彙）、`held`（待連結罰則）、`anchoring`（錨定）、`rows`（共用寫入原語）、
`enums`、`errors`。從定義它的子模組匯入，這裡不匯出。
"""
