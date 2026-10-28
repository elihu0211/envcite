using System.Diagnostics;

using Grpc.Core;

using Proto = EnvCite.Consult.V2;

namespace EnvCite.Consult;

/// <summary>
/// gRPC 端點：把一次管線執行轉成證據或拒答。本服務不執行語言模型，因此沒有串流：證據一次備齊，
/// 由呼叫端交給它自己的模型。
/// </summary>
internal sealed partial class ConsultationGrpcService(GroundingPipeline pipeline, ILogger<ConsultationGrpcService> logger)
    : Proto.ConsultationService.ConsultationServiceBase
{
    public static string ServiceName => Proto.ConsultationService.Descriptor.FullName;

    public override async Task<Proto.GroundResponse> Ground(Proto.GroundRequest request, ServerCallContext context)
    {
        var tenant = context.RequestHeaders.GetValue("x-tenant-id");
        if (string.IsNullOrEmpty(tenant))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "x-tenant-id is required"));
        }

        var requestId = context.RequestHeaders.GetValue("x-request-id") ?? "-";
        LogGround(logger, tenant, requestId);

        string[] unknown = [.. request.Ontologies.Where(o => !Grounding.CodePrefix.ContainsKey(o)).Distinct().Order(StringComparer.Ordinal)];
        if (unknown.Length > 0)
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, $"unknown ontology: {string.Join(", ", unknown)}"));
        }

        // 本體的 RpcException 不攔：伺服器以它的狀態與 trailer 原樣回給呼叫端。無效的 as_of 仍是 INVALID_ARGUMENT、
        // 本體不可用仍是 UNAVAILABLE（可重試）；吞成 UNKNOWN，呼叫端就無從區分參數錯誤與可重試的不可用。
        var outcome = await pipeline.RunAsync(request.Question, request.AsOf, context.CancellationToken);
        return outcome switch
        {
            Refusal refusal => new Proto.GroundResponse
            {
                Refused = new Proto.Refused { Reason = ToProto(refusal.Reason), Detail = refusal.Detail },
            },
            Evidence evidence => new Proto.GroundResponse
            {
                Evidence = new Proto.Evidence
                {
                    Citations =
                    {
                        evidence.Citations.Select(c => new Proto.Citation
                        {
                            Code = c.Code, Iri = c.Iri, Label = c.Label, Text = c.Text, Ontology = c.Ontology,
                        }),
                    },
                    Confidence = evidence.Confidence switch
                    {
                        Confidence.High => Proto.Confidence.High,
                        _ => Proto.Confidence.Low,
                    },
                },
            },
            _ => throw new UnreachableException(),
        };
    }

    private static Proto.Refused.Types.Reason ToProto(RefusalReason reason) => reason switch
    {
        RefusalReason.NoEvidence => Proto.Refused.Types.Reason.NoEvidence,
        RefusalReason.OutOfScope => Proto.Refused.Types.Reason.OutOfScope,
        RefusalReason.MissingInput => Proto.Refused.Types.Reason.MissingInput,
        _ => throw new UnreachableException(),
    };

    [LoggerMessage(Level = LogLevel.Information, Message = "Ground tenant={Tenant} request_id={RequestId}")]
    private static partial void LogGround(ILogger logger, string tenant, string requestId);
}