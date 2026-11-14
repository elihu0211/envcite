"""gRPC servicer：`OntologyService` 的 gRPC 邊界。

法規、圖譜、檢索三組端點（`endpoints/`）做實事；這裡只把每個 RPC 轉給它們，並在邊界以
`errors.maps_errors` 把領域錯誤轉成帶 ErrorInfo 的 gRPC 狀態，端點本身不碰 `context.abort`。
"""

import grpc
from opentelemetry.metrics import Meter

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite.ontology.v2 import ontology_pb2_grpc
from envcite_ontology import errors
from envcite_ontology.embedding import Embedder
from envcite_ontology.endpoints.graph import GraphApi
from envcite_ontology.endpoints.laws import LawApi
from envcite_ontology.endpoints.search import SearchApi
from envcite_ontology.law_names import LawNames
from envcite_ontology.reranking import Reranks
from envcite_ontology.search_tuning import SearchTuning
from envcite_ontology.store.ports import LawGraphReader, LawReader, Retrieval
from envcite_segmenter import Segmenter


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    """`OntologyService` 的 servicer：取法規、列條文與失效條文、混合檢索條文。"""

    def __init__(self, laws: LawApi, graph: GraphApi, search: SearchApi) -> None:
        self.laws = laws
        self.graph = graph
        self.search = search

    @errors.maps_errors
    def GetLaw(self, request: pb.GetLawRequest, context: grpc.ServicerContext) -> pb.GetLawResponse:
        """依自然鍵取得一部法規；不存在回 `LAW_NOT_FOUND`。"""
        return self.laws.get_law(request, context)

    @errors.maps_errors
    def ListArticles(
        self, request: pb.ListArticlesRequest, context: grpc.ServicerContext
    ) -> pb.ListArticlesResponse:
        """依閱讀順序列出一部法規的條文。"""
        return self.laws.list_articles(request, context)

    @errors.maps_errors
    def ListLaws(
        self, request: pb.ListLawsRequest, context: grpc.ServicerContext
    ) -> pb.ListLawsResponse:
        """列出 `as_of` 當日有有效版本的法規摘要與授權母法關聯。"""
        return self.laws.list_laws(request, context)

    @errors.maps_errors
    def GetArticleByIri(
        self, request: pb.GetArticleByIriRequest, context: grpc.ServicerContext
    ) -> pb.GetArticleByIriResponse:
        """依條文版本的標準 IRI 取得單一條文全文與中繼資料。"""
        return self.laws.get_article_by_iri(request, context)

    @errors.maps_errors
    def GetLawGraph(
        self, request: pb.GetLawGraphRequest, context: grpc.ServicerContext
    ) -> pb.GetLawGraphResponse:
        """取得以特定法規為焦點（或依分類總覽）的多層授權知識圖譜。"""
        return self.graph.get_law_graph(request, context)

    @errors.maps_errors
    def ListInvalidations(
        self, request: pb.ListInvalidationsRequest, context: grpc.ServicerContext
    ) -> pb.ListInvalidationsResponse:
        """列出 `as_of` 當日因違憲而失效的條文單元。"""
        return self.laws.list_invalidations(request, context)

    @errors.maps_errors
    def SearchArticles(
        self, request: pb.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> pb.SearchArticlesResponse:
        """在 `as_of` 當日有效的條文上做混合檢索，相關度由高到低。"""
        return self.search.search_articles(request, context)


def build_servicer(
    laws: LawReader,
    graph: LawGraphReader,
    retrieval: Retrieval,
    segmenter: Segmenter,
    law_names: LawNames,
    *,
    embedder: Embedder | None = None,
    reranker: Reranks | None = None,
    tuning: SearchTuning | None = None,
    meter: Meter | None = None,
) -> OntologyServicer:
    """把儲存庫接成三組端點，再接成 servicer。"""
    return OntologyServicer(
        LawApi(laws),
        GraphApi(laws, graph),
        SearchApi(
            laws,
            retrieval,
            segmenter,
            law_names,
            embedder=embedder,
            reranker=reranker,
            tuning=tuning,
            meter=meter,
        ),
    )
