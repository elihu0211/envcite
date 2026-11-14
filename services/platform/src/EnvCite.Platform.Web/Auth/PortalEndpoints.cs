using System.Security.Claims;

using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Web.Auth;

public static class PortalEndpoints
{
    public static IEndpointRouteBuilder MapPortalAuthEndpoints(this IEndpointRouteBuilder app)
    {
        MapConversationEndpoints(app);

        // POST + antiforgery：不能被別的網站用連結或圖片誘導登出。沒有表單參數可綁，所以自己驗證。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/logout", async (HttpContext context, IAntiforgery antiforgery, IPortalAccount account) =>
        {
            try
            {
                await antiforgery.ValidateRequestAsync(context);
            }
            catch (AntiforgeryValidationException)
            {
                return Results.BadRequest();
            }

            // 路徑上的租戶不存在，就當作這個路徑不存在。
            if (account.CurrentTenant is not { } tenant)
                return Results.NotFound();

            await account.SignOutAsync(context.User, context.RequestAborted);
            return Results.Redirect(PortalPaths.Login(tenant.Identifier));
        }).AllowAnonymous();

        // 以 Google 登入的起點：POST + antiforgery（不是連結：別的網站不能用連結或圖片把人送去 Google 再登入進這個租戶）。
        // 功能沒開、或路徑上的租戶不存在，就當作這個路徑不存在。路徑與 PortalPaths.GoogleChallenge 相同。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/login/google", async (HttpContext context, IAntiforgery antiforgery, IPortalAccount account, IGoogleSignInAvailability google) =>
        {
            if (!google.Enabled || account.CurrentTenant is not { } tenant)
                return Results.NotFound();

            try
            {
                await antiforgery.ValidateRequestAsync(context);
            }
            catch (AntiforgeryValidationException)
            {
                return Results.BadRequest();
            }

            // 登入後要去的地方經同一份 SafeReturnUrl（只准留在這個租戶的入口之下），回呼那一段再檢查一次。
            var routes = SignInRoutes.Portal(tenant.Identifier);
            var form = await context.Request.ReadFormAsync(context.RequestAborted);
            var returnUrl = routes.SafeReturnUrl(form["returnUrl"].ToString());
            var callback = PortalPaths.GoogleCallback(tenant.Identifier);
            return Results.Challenge(
                account.GoogleChallenge(returnUrl == routes.Home ? callback : $"{callback}?returnUrl={Uri.EscapeDataString(returnUrl)}"),
                [PlatformAuth.PortalGoogleScheme]);
        }).AllowAnonymous().RequireRateLimiting(PlatformAuth.PortalSignInRateLimit);

        // 以 Google 登入的第二段：Google 回到全站固定的回呼位址後，轉來這裡在租戶的路徑下完成登入。
        // 成敗都導向，不渲染頁面；失敗只說失敗（登入頁依 google=failed 顯示同一句話）。路徑與 PortalPaths.GoogleCallback 相同。
        app.MapGet($"{PortalPaths.Prefix}/{{tenant}}/login/google/callback", async (HttpContext context, IPortalAccount account, IGoogleSignInAvailability google) =>
        {
            if (!google.Enabled || account.CurrentTenant is not { } tenant)
                return Results.NotFound();

            var routes = SignInRoutes.Portal(tenant.Identifier);
            var returnUrl = context.Request.Query["returnUrl"].ToString();
            var next = await account.SignInWithGoogleAsync(context.RequestAborted) switch
            {
                SignInStatus.Succeeded => routes.SafeReturnUrl(returnUrl),
                SignInStatus.MustChangePassword => routes.Password,
                SignInStatus.RequiresTwoFactor => routes.MfaLink(returnUrl),
                _ => $"{routes.Login}?google=failed",
            };
            return Results.Redirect(next);
        }).AllowAnonymous();

        // 換介面語言：登入前後都要能用；導回的位置只限這個租戶的入口之下。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/language", (HttpContext context, IAntiforgery antiforgery, IPortalAccount account) =>
            account.CurrentTenant is not { } tenant
                ? Task.FromResult(Results.NotFound())
                : Localization.SwitchAsync(context, antiforgery, SignInRoutes.Portal(tenant.Identifier).SafeReturnUrl)).AllowAnonymous();

        // 設定外觀：同語言，只是這個瀏覽器的偏好（cookie）；路徑上的租戶不存在就當作這個路徑不存在。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/appearance", (HttpContext context, IAntiforgery antiforgery, IAppearancePreference preference, IPortalAccount account) =>
            account.CurrentTenant is not { } tenant
                ? Task.FromResult(Results.NotFound())
                : AppearanceModule.SetAsync(context, antiforgery, preference, SignInRoutes.Portal(tenant.Identifier).SafeReturnUrl)).AllowAnonymous();

        return app;
    }

    /// <summary>
    /// 對話的釘選、重新命名、刪除：側欄與對話紀錄頁選單裡的表單 POST 到這裡。附 antiforgery、只有登入的入口成員，
    /// 而且只動自己的對話（<see cref="IConversationHistory"/> 依使用者 Id 篩選）；不是自己的、不存在的結果一樣，不洩漏它存不存在。
    /// 做完回到原來的頁面（取自 Referer，只接受這個租戶入口之下的路徑）。
    /// </summary>
    private static void MapConversationEndpoints(IEndpointRouteBuilder app)
    {
        var route = $"{PortalPaths.Prefix}/{{tenant}}/c/{{id:guid}}";

        app.MapPost($"{route}/pin", (HttpContext context, IAntiforgery antiforgery, IPortalAccount account, IConversationHistory history, Guid id) =>
            ManageConversationAsync(context, antiforgery, account, id, deletes: false, (user, form, ct) => history.PinAsync(user, id, form["pinned"] == "true", ct)))
            .RequireAuthorization(PlatformAuth.PortalPolicy);

        app.MapPost($"{route}/rename", (HttpContext context, IAntiforgery antiforgery, IPortalAccount account, IConversationHistory history, Guid id) =>
            ManageConversationAsync(context, antiforgery, account, id, deletes: false, (user, form, ct) => history.RenameAsync(user, id, form["title"].ToString(), ct)))
            .RequireAuthorization(PlatformAuth.PortalPolicy);

        app.MapPost($"{route}/delete", (HttpContext context, IAntiforgery antiforgery, IPortalAccount account, IConversationHistory history, Guid id) =>
            ManageConversationAsync(context, antiforgery, account, id, deletes: true, (user, _, ct) => history.DeleteAsync(user, id, ct)))
            .RequireAuthorization(PlatformAuth.PortalPolicy);

        app.MapGet($"{PortalPaths.Prefix}/{{tenant}}/citations/article", async (
            HttpContext context, string? iri, ICitationArticleService citations, CancellationToken ct) =>
        {
            var outcome = await citations.GetByIriAsync(context.User, iri, ct);
            return outcome.Status switch
            {
                AdminStatus.Ok => Results.Ok(outcome.Value),
                AdminStatus.Invalid => Results.ValidationProblem(new Dictionary<string, string[]> { ["iri"] = [outcome.Message ?? "Invalid IRI"] }),
                AdminStatus.NotFound => Results.NotFound(),
                AdminStatus.Forbidden => Results.Forbid(),
                _ => Results.Problem(statusCode: StatusCodes.Status503ServiceUnavailable, detail: outcome.Message),
            };
        }).RequireAuthorization(PlatformAuth.PortalPolicy);
    }

    private static async Task<IResult> ManageConversationAsync(
        HttpContext context,
        IAntiforgery antiforgery,
        IPortalAccount account,
        Guid id,
        bool deletes,
        Func<ClaimsPrincipal, IFormCollection, CancellationToken, Task<bool>> action)
    {
        try
        {
            await antiforgery.ValidateRequestAsync(context);
        }
        catch (AntiforgeryValidationException)
        {
            return Results.BadRequest();
        }

        if (account.CurrentTenant is not { } tenant)
            return Results.NotFound();

        var form = await context.Request.ReadFormAsync(context.RequestAborted);
        _ = await action(context.User, form, context.RequestAborted);
        return Results.Redirect(BackTo(context, tenant.Identifier, id, deletes));
    }

    /// <summary>回到送出表單的那一頁；刪掉的正是正在看的那串，就回首頁（不回一個找不到的頁面）。路徑一律經 <see cref="SignInRoutes.SafeReturnUrl"/>。</summary>
    private static string BackTo(HttpContext context, string tenantIdentifier, Guid id, bool deleted)
    {
        var routes = SignInRoutes.Portal(tenantIdentifier);
        var back = Uri.TryCreate(context.Request.Headers.Referer.ToString(), UriKind.Absolute, out var referer) ? referer.PathAndQuery : null;
        return deleted && back?.StartsWith(PortalPaths.Conversation(tenantIdentifier, id), StringComparison.OrdinalIgnoreCase) == true
            ? routes.Home
            : routes.SafeReturnUrl(back);
    }
}