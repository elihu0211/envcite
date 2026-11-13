"""本體服務的自訂指標（OpenTelemetry；沒設匯出端點時是無操作，見 `envcite_grpc.telemetry`）。

兩類：
- **推論的服務面**：每次 `Evaluate` 的耗時與結果（ok、invalid、busy、deadline、error）、同時進行數。
  `busy` 的比率是擴容的訊號。
- **標註管線的資料面**：各狀態的陳述數、各驗證方法最近一次的結果分布，讀自 0020 的健康檢視。
  閘門通過率是這個系統最核心的健康指標：確認的陳述為零，推論就什麼也推不出。

標籤只用有限集合（結果、狀態、方法、判定）；租戶與案件內容不進指標。
"""

import logging
from collections.abc import Iterable
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Meter, Observation

from envcite_ontology.store import OntologyStore

log = logging.getLogger(__name__)

METER_NAME = "envcite.ontology"


class ReasoningMetrics:
    """`Evaluate` 的服務面指標。"""

    def __init__(self, meter: Meter | None = None) -> None:
        meter = meter or metrics.get_meter(METER_NAME)
        self.duration = meter.create_histogram(
            "envcite.reasoning.evaluate.duration",
            unit="s",
            description=(
                "Evaluate 從收到到回應的耗時，含等名額；"
                "outcome 為 ok、invalid、busy、deadline、error。"
            ),
        )
        self.in_flight = meter.create_up_down_counter(
            "envcite.reasoning.evaluate.in_flight",
            description="正在執行（已取得名額）的 Evaluate 數。",
        )


def register_annotation_health(store: OntologyStore, meter: Meter | None = None) -> None:
    """標註管線的資料面：每次收集時讀兩個健康檢視（兩個小聚合，成本可忽略）。"""
    meter = meter or metrics.get_meter(METER_NAME)

    def statements(_: CallbackOptions) -> Iterable[Observation]:
        for state, count in _rows(
            store, "SELECT state, statements FROM annotation.statement_health"
        ):
            yield Observation(count, {"state": state})

    def gates(_: CallbackOptions) -> Iterable[Observation]:
        for method, verdict, count in _rows(
            store, "SELECT method, verdict, statements FROM annotation.gate_health"
        ):
            yield Observation(int(count), {"method": str(method), "verdict": str(verdict)})

    meter.create_observable_gauge(
        "envcite.annotation.statements",
        callbacks=[statements],
        description="各審核狀態的陳述數；confirmed 為 0 表示推論沒有可用的規範。",
    )
    meter.create_observable_gauge(
        "envcite.verification.gate_results",
        callbacks=[gates],
        description="各驗證方法最近一次的結果分布（pass、fail、inconclusive、not_applicable）。",
    )


def _rows(store: OntologyStore, sql: str) -> list[tuple[Any, ...]]:
    try:
        with store.read_connection(timeout=5.0) as conn:
            return [tuple(row) for row in conn.execute(sql)]
    except Exception:  # 指標回呼不能拖垮服務：資料庫暫時查不到就這一輪沒有資料
        log.warning("annotation health metrics unavailable", exc_info=True)
        return []
