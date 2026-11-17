"""寫入 `guidance` 的推導來源：改變寫入的公文內容、錨定或停止適用的規則時遞增版本。"""

from canon.rules import Rule

#: 取得是規則推導，不是人或模型；改變寫入的公文內容、錨定或停止適用的規則時遞增版本。
RULE = Rule("canon-guidance-sync", 2)

#: `guidance.load` 的 `derivation`
DERIVATION = RULE.derivation
