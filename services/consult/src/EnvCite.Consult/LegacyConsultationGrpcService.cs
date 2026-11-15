using Google.Protobuf;

using Grpc.Core;

using Current = EnvCite.Consult.V3;
using Legacy = EnvCite.Consult.V2;

namespace EnvCite.Consult;

/// <summary>
/// 過渡：以舊的套件名 <c>envcite.consult.v2</c> 繼續回應尚未升級的呼叫端。兩個套件的訊息編號一致，
/// 經線路格式轉換後交給同一個端點；呼叫端都改用 v3 後，整個類別與 <c>Program.cs</c> 的登記一併刪除。
/// </summary>
internal sealed class LegacyConsultationGrpcService(GroundingPipeline pipeline, GroundingMetrics metrics, ILogger<ConsultationGrpcService> logger)
    : Legacy.ConsultationService.ConsultationServiceBase
{
    private readonly ConsultationGrpcService _current = new(pipeline, metrics, logger);

    public static string ServiceName => Legacy.ConsultationService.Descriptor.FullName;

    public override async Task<Legacy.GroundResponse> Ground(Legacy.GroundRequest request, ServerCallContext context)
    {
        var response = await _current.Ground(Current.GroundRequest.Parser.ParseFrom(request.ToByteArray()), context);
        return Legacy.GroundResponse.Parser.ParseFrom(response.ToByteArray());
    }
}