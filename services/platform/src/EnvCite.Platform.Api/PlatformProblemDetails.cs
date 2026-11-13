namespace EnvCite.Platform.Api;

/// <summary>
/// 未捕捉例外的回應。<c>UseExceptionHandler()</c> 交給 <see cref="IProblemDetailsService"/> 寫出 problem+json
/// （只對接受 JSON 的呼叫端；其餘維持空本文的 500）。這裡補上穩定的 <c>code</c>，與營運操作的失敗同形
/// （見 <see cref="OutcomeResults"/>），呼叫端依代碼判斷；<c>detail</c> 是固定的英文，例外的訊息與堆疊不出現在回應裡。
/// </summary>
internal static class PlatformProblemDetails
{
    public static IServiceCollection AddPlatformProblemDetails(this IServiceCollection services) =>
        services.AddProblemDetails(options => options.CustomizeProblemDetails = context =>
        {
            var problem = context.ProblemDetails;
            if (problem.Status != StatusCodes.Status500InternalServerError || problem.Extensions.ContainsKey("code"))
                return;

            problem.Detail = OutcomeCodes.English[OutcomeCodes.InternalError];
            problem.Extensions["code"] = OutcomeCodes.InternalError;
        });
}