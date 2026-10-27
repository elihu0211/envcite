using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 結果轉成 API 的錯誤回應：<c>detail</c> 與錯誤訊息永遠是英文（<see cref="AdminOutcome{T}.Message"/>），
/// 另帶穩定的 <c>code</c>（見 <see cref="OutcomeCodes"/>），呼叫端依 <c>code</c> 判斷、不依賴文字。
/// </summary>
public static class OutcomeResults
{
    private static Dictionary<string, object?>? CodeOf<T>(AdminOutcome<T> outcome) =>
        outcome.Code is null ? null : new Dictionary<string, object?> { ["code"] = outcome.Code };

    public static IResult ValidationProblem<T>(this AdminOutcome<T> outcome, string field = "request") =>
        Results.ValidationProblem(
            new Dictionary<string, string[]> { [field] = [outcome.Message ?? "Invalid request."] },
            extensions: CodeOf(outcome));

    public static IResult Problem<T>(this AdminOutcome<T> outcome, int statusCode, string? fallback = null) =>
        Results.Problem(outcome.Message ?? fallback, statusCode: statusCode, extensions: CodeOf(outcome));
}