"""寫入 `guidance` 的推導來源：改變寫入的公文內容、錨定或停止適用的規則時遞增版本。"""

PRODUCER = "envcite-guidance-sync"


#: 改變寫入的公文內容、錨定或停止適用的規則時遞增
RULE_VERSION = "2"

#: `guidance.load` 的 `derivation`：取得是規則推導，不是人或模型
DERIVATION = {"method": "rule", "agent": PRODUCER, "agent_version": RULE_VERSION}
