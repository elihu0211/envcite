"""取得與驗收的政策：重試政策、隔離的比例，與整批驗收的上限。"""

from envcite_jobkit import Policy
from envcite_ontology.ingest import audit

POLICY = Policy(
    name="history",
    open_action="exit",
    max_attempts=4,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)


#: 隔離的法規占列舉數超過此比例，本次判定失敗
COVERAGE_TOLERANCE = 0.01


#: 整批驗收：名稱 → (說明, 上限)；對不到的上限因來源而異（`HistorySource.unmatched_limit`）
LIMITS = {
    "rejected": ("有 error 而不寫的占對到的法規", 0.05),
    "degraded_articles": ("切分退回整條的條占歷史版本的條", audit.LIMITS["degraded_articles"][1]),
    # 寫完才知道，不在寫入前的驗收裡；見 `_write_gate`
    "write_rejected": ("寫入時被資料庫擋下的占對到的法規", 0.05),
}
