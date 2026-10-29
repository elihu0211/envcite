---
type: reference
applies-to: all
owns: [tokenizer-identity]
---

# envcite-segmenter

中文斷詞，詞典由語料產生，無第三方依賴。索引端與查詢端必須用同一個分詞器，否則詞彙臂會安靜地變差；識別碼讓兩端不一致時查得出來（見「tokenizer id」）。

## 切法

lexicon 內的用語由左至右取最長者，整個保留；其餘漢字切成相鄰二字組，英數字連續成一個詞。判準是法定用語（如「事業廢棄物」）出現時是否被切成完整一個 token，真值取自法條自己的定義條。量測方法與重跑方式見 [`docs/segmentation-measurement.md`](docs/segmentation-measurement.md)。

## 詞典來源

語料中的法規名稱；隨語料匯入自動成長。

## tokenizer id

`<ALGORITHM>+dict-<詞典雜湊前綴>`，寫進 `retrieval.passage.tokenizer`。`ALGORITHM` 在切法改變時遞增，測試以固定輸出把關。

詞典或切法變動會改變識別碼，既有的 `tsv` 與新查詢因而對不上，這是刻意的：識別碼不符的列由檢索投影重切，重切完成前不參與字面臂。
