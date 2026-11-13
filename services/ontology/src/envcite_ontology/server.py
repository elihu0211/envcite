"""gRPC 伺服器啟動；環境變數：ENVCITE_ONTOLOGY_DSN（必填）、ENVCITE_ONTOLOGY_GRPC_PORT。"""

import logging
from collections.abc import Sequence
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from opentelemetry.metrics import Meter

from envcite.ontology.v2 import ontology_pb2_grpc, reasoning_pb2_grpc
from envcite_grpc import SERVER_OPTIONS, configure_telemetry, stop_on_sigterm
from envcite_ontology.embedding import Embedder
from envcite_ontology.reasoning_service import ReasoningServicer
from envcite_ontology.reranking import Reranks
from envcite_ontology.service import DEFAULT_RERANK_CANDIDATES, OntologyServicer
from envcite_ontology.settings import ServerSettings, load
from envcite_ontology.store import OntologyStore
from envcite_ontology.telemetry import register_annotation_health
from envcite_ontology.verification import config
from envcite_segmenter import Segmenter

SERVICE_NAME = "envcite.ontology.v2.OntologyService"
REASONING_SERVICE_NAME = "envcite.ontology.v2.ReasoningService"

WORKERS = 10
#: 同時進行的 RPC 上限。固定大小的執行緒池之外，超過的請求排隊；上限讓排隊有界，超過就
#: 以 RESOURCE_EXHAUSTED 立即失敗，而不是無限等下去。
MAX_CONCURRENT_RPCS = 2 * WORKERS
#: 同時向嵌入端點取查詢向量的上限。必須小於 WORKERS：慢端點不能占滿執行緒，名額滿了
#: 立刻改走字面臂，查詢仍成功。
QUERY_EMBED_SLOTS = 2
#: 同時向精排端點打分的上限，同樣必須小於 WORKERS。精排一次約 1 至 2 秒，
#: 比嵌入慢得多，所以名額較寬，但仍有界：名額滿了立刻退回精排前的次序，查詢仍成功。
RERANK_SLOTS = 4
#: 精排端點的單次上限（秒）。實測 30 條約 1.3 秒，留到約兩倍；比它慢就不如退回精排前的次序。
RERANK_TIMEOUT = 3.0


def build_server(
    store: OntologyStore,
    address: str = "[::]:0",
    options: Sequence[tuple[str, int]] = SERVER_OPTIONS,
    interceptors: Sequence[grpc.ServerInterceptor] = (),
    health_servicer: health.HealthServicer | None = None,
    embedder: Embedder | None = None,
    lexical_max_df: float = 0.0,
    guidance_max_df: float = 0.0,
    reranker: Reranks | None = None,
    rerank_candidates: int = DEFAULT_RERANK_CANDIDATES,
    meter: Meter | None = None,
) -> tuple[grpc.Server, int]:
    """建立但不啟動；回傳伺服器與實際綁定的埠號。`health_servicer` 讓呼叫端在關閉時切換狀態。

    `lexical_max_df` 是字面臂略過高頻詞的段落比例上限（見 `ServerSettings`）；0 為不略過。
    `guidance_max_df` 是函釋入口容許的命中函釋占比上限；0 為不限制。
    `reranker` 是請求帶 `rerank` 時用的精排模型（None 為不精排）；`rerank_candidates` 是它看多深。
    `meter` 是指標的 Meter（None 為全域的；測試注入記憶體內的讀取器）。
    """
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=WORKERS),
        interceptors=list(interceptors),
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=options,
    )
    # lexicon 啟動時只讀一次：新匯入的用語須重啟才進入查詢。
    segmenter = Segmenter(store.lexicon())
    law_names = store.law_names()
    ontology_pb2_grpc.add_OntologyServiceServicer_to_server(
        OntologyServicer(
            store,
            segmenter,
            law_names,
            embedder,
            lexical_max_df,
            embed_slots=QUERY_EMBED_SLOTS,
            guidance_max_df=guidance_max_df,
            reranker=reranker,
            rerank_slots=RERANK_SLOTS,
            rerank_candidates=rerank_candidates,
            meter=meter,
        ),
        server,
    )
    reasoning_pb2_grpc.add_ReasoningServiceServicer_to_server(ReasoningServicer(store), server)
    reflection.enable_server_reflection(
        (
            SERVICE_NAME,
            REASONING_SERVICE_NAME,
            health.SERVICE_NAME,
            reflection.SERVICE_NAME,
        ),
        server,
    )

    health_servicer = health_servicer or health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in ("", SERVICE_NAME, REASONING_SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    port = server.add_insecure_port(address)
    return server, port


def main() -> None:
    """`envcite-ontology`：啟動 gRPC 伺服器（法規查詢、檢索與推論），阻塞到被停止。

    收到 SIGTERM 時先排空進行中的呼叫再結束。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load(ServerSettings)
    store = OntologyStore.from_dsn(settings.dsn)

    # 沒設 OTEL_EXPORTER_OTLP_ENDPOINT 時什麼都不做（見 envcite_grpc.telemetry）。
    interceptors = configure_telemetry("envcite-ontology")
    register_annotation_health(store)
    health_servicer = health.HealthServicer()
    server, bound = build_server(
        store,
        f"[::]:{settings.grpc_port}",
        interceptors=interceptors,
        health_servicer=health_servicer,
        # 查詢路徑上不重試、逾時短：取不到向量就只用字面臂，不拖慢查詢
        embedder=config.embedder_from_settings(settings, timeout=5.0),
        lexical_max_df=settings.lexical_max_df,
        guidance_max_df=settings.guidance_max_df,
        # 精排同樣不重試；逾時以呼叫端剩下的期限為準，這裡只是上限
        reranker=config.reranker_from_settings(settings, timeout=RERANK_TIMEOUT),
        rerank_candidates=settings.rerank_candidates,
    )
    stop_on_sigterm(server, health_servicer)
    server.start()
    logging.getLogger(__name__).info("ontology gRPC server listening on :%d", bound)
    server.wait_for_termination()
