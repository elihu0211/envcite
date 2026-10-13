using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Ontology;

public static class ReasoningEndpoints
{
    public const string EvaluateOperation = "reasoning.evaluate";

    /// <summary>包在內部 <c>ReasoningService.Evaluate</c> 外的對外 REST；成功的呼叫計量一次。</summary>
    public static RouteGroupBuilder MapReasoningEndpoints(this RouteGroupBuilder v1)
    {
        // POST：案件事實是客戶案件的內容，不放在網址裡。
        v1.MapPost("/reasoning/evaluate", async (EvaluateReasoningRequest request,
            ReasoningService.ReasoningServiceClient client, IOptions<OntologyOptions> options,
            IUsageRecorder usage, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(request.ConductOn) || string.IsNullOrWhiteSpace(request.DecidedOn))
            {
                return Results.ValidationProblem(new Dictionary<string, string[]>
                {
                    ["request"] = ["conductOn and decidedOn are required."],
                });
            }

            EvaluateRequest grpcRequest;
            try
            {
                grpcRequest = ReasoningMapper.ToGrpc(request);
            }
            catch (ArgumentException ex)
            {
                return Results.ValidationProblem(new Dictionary<string, string[]> { ["facts"] = [ex.Message] });
            }

            try
            {
                var response = await client.EvaluateAsync(grpcRequest,
                    deadline: DateTime.UtcNow.Add(options.Value.ReasoningTimeout), cancellationToken: ct);
                await usage.RecordAsync(EvaluateOperation, 1, ct);
                return Results.Ok(ReasoningMapper.ToDto(response));
            }
            catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
            {
                return Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [ex.Status.Detail] });
            }
            catch (RpcException ex) when (ex.StatusCode == StatusCode.ResourceExhausted)
            {
                // 推論容量用盡：立即失敗而不是排隊，呼叫端稍後重試（唯讀、確定性，重試安全）。
                return Results.Problem("Reasoning capacity is in use; retry shortly.",
                    statusCode: StatusCodes.Status503ServiceUnavailable,
                    extensions: new Dictionary<string, object?> { ["retryable"] = true });
            }
            catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
            {
                return Results.Problem("Ontology service unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
            }
        })
            .WithName("EvaluateReasoning")
            .WithTags("推論")
            .WithSummary("對案件事實推論")
            .WithDescription(
                "依行為日與裁處日當天有效、已通過驗證的規範陳述，對案件事實推論，回傳**候選**結論與依據；系統不裁量、不判決。"
                + "`atDecision` 是裁處時之法律（行政罰法第 5 條的預設），`earlier` 是行為日到裁處日之間陳述組與裁處時不同的法律，"
                + "「最有利於受處罰者」由呼叫端依第 5 條但書選擇。"
                + "沒有通過驗證的陳述就沒有結論：`verdicts` 為空不表示合規。`uncoveredLawIds` 列出語料沒有當時版本的法規，"
                + "其規定缺席不代表當時沒有規定。`administrativePenalty` 省略的欄位是「未知」，不是「否」。"
                + "推論是唯讀、確定性的，重試安全。成功計量為一次 `reasoning.evaluate`；推論容量用盡回 503（`retryable`）。")
            .Produces<ReasoningResultDto>()
            .ProducesValidationProblem()
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        return v1;
    }
}