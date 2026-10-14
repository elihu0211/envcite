using System.Security.Claims;

using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Members;

public sealed record CreateOperatorRequest(string? Email, string? DisplayName);

/// <summary>
/// <c>/admin/operators</c>：營運者帳號的管理，持營運者金鑰（<c>X-Admin-Key</c>）才能用，沒有自助註冊。
/// 與 <see cref="MembersAdminEndpoints"/> 對應：建立與重設密碼的回應含一次性的初始密碼（只此一次，資料庫只存雜湊），
/// 呼叫端要自己安全地處理，回應內容不會被記進日誌或稽核。
/// </summary>
public static class OperatorsAdminEndpoints
{
    public static RouteGroupBuilder MapOperatorsAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapGet("/operators", async (IOperatorAdmin operators, CancellationToken ct) =>
        {
            var list = await operators.ListAsync(ct);
            return list.IsOk ? Results.Ok(list.Value) : Problem(list);
        });

        admin.MapPost("/operators", async (CreateOperatorRequest request, IOperatorAdmin operators, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var created = await operators.CreateAsync(user, request.Email ?? string.Empty, request.DisplayName, ct);
            return created.IsOk ? Results.Created("/admin/operators", created.Value) : Problem(created);
        });

        admin.MapPost("/operators/{operatorId}/password-reset", async (string operatorId, IOperatorAdmin operators, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var reset = await operators.ResetPasswordAsync(user, operatorId, ct);
            return reset.IsOk ? Results.Ok(reset.Value) : Problem(reset);
        });

        admin.MapPost("/operators/{operatorId}/two-factor-reset", async (string operatorId, IOperatorAdmin operators, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var reset = await operators.ResetTwoFactorAsync(user, operatorId, ct);
            return reset.IsOk ? Results.Ok(reset.Value) : Problem(reset);
        });

        admin.MapPost("/operators/{operatorId}/disable", (string operatorId, IOperatorAdmin operators, ClaimsPrincipal user, CancellationToken ct) =>
            SetDisabledAsync(operators, user, operatorId, disabled: true, ct));

        admin.MapPost("/operators/{operatorId}/enable", (string operatorId, IOperatorAdmin operators, ClaimsPrincipal user, CancellationToken ct) =>
            SetDisabledAsync(operators, user, operatorId, disabled: false, ct));

        return admin;
    }

    private static async Task<IResult> SetDisabledAsync(
        IOperatorAdmin operators, ClaimsPrincipal user, string operatorId, bool disabled, CancellationToken ct)
    {
        var changed = await operators.SetDisabledAsync(user, operatorId, disabled, ct);
        return changed.IsOk ? Results.Ok(changed.Value) : Problem(changed);
    }

    private static IResult Problem<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.NotFound => Results.NotFound(),
        AdminStatus.Conflict => Results.Conflict(),
        AdminStatus.Invalid => Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [outcome.Message ?? "Invalid request."] }),
        _ => Results.StatusCode(StatusCodes.Status500InternalServerError),
    };
}