"""過渡：以舊的套件名 `envcite.ontology.v2` 繼續回應尚未升級的呼叫端。

兩個套件的訊息編號與型別一致，線路上位元組相同，所以同一個 servicer 的方法直接掛在舊的服務名下，
不必轉換訊息。呼叫端都改用 `envcite.canon.v1` 後，整個模組與 `server.py` 的呼叫一併刪除。
"""

import grpc
from google.protobuf import message_factory
from google.protobuf.descriptor import ServiceDescriptor
from google.protobuf.message import Message

LEGACY_SERVICE_NAME = "envcite.ontology.v2.OntologyService"
LEGACY_REASONING_SERVICE_NAME = "envcite.ontology.v2.ReasoningService"


def _serialize(message: Message) -> bytes:
    return message.SerializeToString()


def add_under_name(
    server: grpc.Server, name: str, descriptor: ServiceDescriptor, servicer: object
) -> None:
    """把 `servicer` 的每個方法掛在 `name` 這個服務名下；方法簽章取自 `descriptor`。"""
    handlers: dict[str, grpc.RpcMethodHandler[Message, Message]] = {}
    for method in descriptor.methods:
        if method.client_streaming or method.server_streaming:
            raise ValueError(f"{descriptor.full_name}.{method.name} 是串流；別名只支援一元呼叫")
        request = message_factory.GetMessageClass(method.input_type)
        handlers[method.name] = grpc.unary_unary_rpc_method_handler(
            getattr(servicer, method.name),
            request_deserializer=request.FromString,
            response_serializer=_serialize,
        )
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(name, handlers),))
