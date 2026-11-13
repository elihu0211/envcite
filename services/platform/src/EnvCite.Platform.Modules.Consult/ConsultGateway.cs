using EnvCite.Consult.V2;

using Grpc.Core;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 向 consult 取得封閉證據集：對外 REST、入口的有依據回答與 MCP 工具共用這一份，所以輸入錯誤與暫時不可用的判斷只有一個實作。
/// <b>不計量</b>：計量與重送的語意各呼叫端不同（REST 的 <c>Idempotency-Key</c> 要與回應同一個交易記），由呼叫端記 <see cref="ConsultEndpoints.GroundOperation"/>。
/// 問題文字是客戶案件的內容，不進日誌。
/// </summary>
internal sealed class ConsultGateway(ConsultationService.ConsultationServiceClient consult, IOptions<ConsultOptions> options)
{
    public async Task<AdminOutcome<GroundResponse>> GroundAsync(string? question, string? asOf, CancellationToken cancellationToken)
    {
        try
        {
            return AdminOutcome.Ok(await consult.GroundAsync(
                new GroundRequest { Question = question ?? string.Empty, AsOf = asOf ?? string.Empty },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken));
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return AdminOutcome.Invalid<GroundResponse>(OutcomeCodes.UpstreamInvalidRequest, ex.Status.Detail);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable
            or StatusCode.DeadlineExceeded
            or StatusCode.ResourceExhausted)
        {
            return AdminOutcome.Unavailable<GroundResponse>(OutcomeCodes.ConsultUnavailable);
        }
    }
}