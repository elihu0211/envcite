# envcite-segmenter

中文斷詞，詞典由語料自己產生。

| 往上 | 往旁 |
|------|------|
| [倉庫總覽](../../../README.md) · [`libs/python/AGENTS.md`](../AGENTS.md)（同一分詞器的約束） | 使用端：[`services/ontology`](../../../services/ontology/README.md)（檢索投影與查詢）；量測：[`docs/segmentation-measurement.md`](docs/segmentation-measurement.md) |

**索引端與查詢端必須用同一個分詞器**，否則詞彙臂會安靜地變差——查詢的詞素與索引的詞素不同，命中變少而不是變錯。這個函式庫存在就是為了讓兩端不可能用到不同的東西。

## 為什麼是「語料自產 lexicon ＋ 二字組」

法定用語（如「事業廢棄物」）是立法者定義的一個概念，通用分詞器會把它切開，查它的人就配到講別的概念的條文。判準是「法定用語出現時是否被切成完整一個 token」，真值取自法條自己的定義條。做法：lexicon 內的用語由左至右取最長者，整個保留；其餘漢字切成相鄰的二字組，英數字連續成一個詞。沒有第三方依賴——通用分詞器（jieba 等）要不停更、要不不接受自訂詞典，而**詞典的影響遠大於分詞器本身的選擇**。量測方法與重跑方式見 [`docs/segmentation-measurement.md`](docs/segmentation-measurement.md)。

## 詞典來自哪裡

語料中的法規名稱；定義條自己列的法定用語，隨定義抽取加入。**詞典隨語料匯入自動成長，沒有人要維護它。**

## tokenizer id

`<ALGORITHM>+dict-<詞典雜湊前綴>`（例：`lexbigram-1+dict-ab3dc33b`），寫進 `retrieval.lexical_index.tokenizer`。

`ALGORITHM` 在切法改變時遞增，測試以固定輸出把關。詞典或切法變動會改變識別碼，於是既有的 `content_tsv` 與新的查詢對不上——這是刻意的。**偵測到不符時該做的是重建全部 tsvector**，由匯入器負責；過渡期間識別碼不符的列不參與檢索。
