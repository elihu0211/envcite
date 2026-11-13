using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Ontology;

public static class ReasoningEndpoints
{
    public const string EvaluateOperation = "reasoning.evaluate";

    /// <summary>包在內部 <c>ReasoningService.Evaluate</c> 外的對外 REST；成功的呼叫計量一次。</summary>
    public static RouteGroupBuilder MapReasoningEndpoints(this RouteGroupBuilder v1)
    {
        // POST：案件事實是客戶案件的內容，不放在網址裡。
        v1.MapPost("/reasoning/evaluate", async (EvaluateReasoningRequest request, OntologyGateway gateway, CancellationToken ct) =>
        {
            var outcome = await gateway.EvaluateAsync(request, ct);

            // 推論容量用盡：帶 retryable，呼叫端稍後重試。
            return outcome is { Status: AdminStatus.Unavailable, Code: OutcomeCodes.ReasoningBusy }
                ? Results.Problem(outcome.Message, statusCode: StatusCodes.Status503ServiceUnavailable,
                    extensions: new Dictionary<string, object?> { ["code"] = outcome.Code, ["retryable"] = true })
                : OntologyEndpoints.Present(outcome);
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