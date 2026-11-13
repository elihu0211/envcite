using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web;

/// <summary>
/// 外觀：跟隨系統（預設）、亮色、暗色。預設不存任何東西、不輸出 <c>data-theme</c>，只靠 <c>prefers-color-scheme</c>；
/// 使用者在個人設定手動選了亮色或暗色，才由這個偏好覆寫。
/// </summary>
public enum Appearance
{
    /// <summary>跟隨瀏覽器與作業系統（<c>prefers-color-scheme</c>）。</summary>
    System,

    Light,

    Dark,
}

public static class AppearanceNames
{
    /// <summary>表單的欄位名；值是 <see cref="Name"/> 的三個詞。</summary>
    public const string Field = "appearance";

    /// <summary>cookie、表單與 <c>data-theme</c> 共用的詞：<c>system</c>、<c>light</c>、<c>dark</c>（跟隨系統不輸出 <c>data-theme</c>）。</summary>
    public static string Name(this Appearance appearance) => appearance switch
    {
        Appearance.Light => "light",
        Appearance.Dark => "dark",
        _ => "system",
    };

    /// <summary>只認三個詞，大小寫不論；其餘（空白、竄改的值）都不是偏好，回傳 null。</summary>
    public static Appearance? Parse(string? value) =>
        Enum.GetValues<Appearance>().Select(a => (Appearance?)a).FirstOrDefault(a => string.Equals(a!.Value.Name(), value, StringComparison.OrdinalIgnoreCase));
}

/// <summary>
/// 外觀偏好的 cookie。用 ASP.NET Core 慣用的 <see cref="CookieBuilder"/> 描述（與 Antiforgery、Identity 同一種），
/// 名稱、有效期與屬性在這裡集中，不散在端點與元件裡。它只是偏好，不認證任何東西，也不含個資。
/// </summary>
public sealed class AppearanceOptions
{
    /// <summary>
    /// 一年。<c>HttpOnly</c>：只有伺服器讀（輸出 <c>data-theme</c>），寫入也經過伺服器的端點（antiforgery 驗證、值查表），
    /// 腳本只負責「立即預覽」，不碰 cookie。<c>IsEssential</c>：它是使用者要求的功能，不是追蹤。
    /// </summary>
    public CookieBuilder Cookie { get; } = new()
    {
        Name = "ep-appearance",
        Path = "/",
        MaxAge = TimeSpan.FromDays(365),
        HttpOnly = true,
        IsEssential = true,
        SameSite = SameSiteMode.Lax,
        SecurePolicy = CookieSecurePolicy.SameAsRequest,
    };
}

/// <summary>讀取與寫入外觀偏好。無狀態，註冊成單例。</summary>
public interface IAppearancePreference
{
    /// <summary>
    /// 這個請求的偏好；沒有 cookie、cookie 被清掉或值不合法時是 <see cref="Appearance.System"/>。
    /// </summary>
    Appearance Read(HttpContext context);

    /// <summary>寫入偏好。選回「跟隨系統」是刪掉 cookie：回到從沒設定過的狀態，而不是存一個「system」。</summary>
    void Write(HttpContext context, Appearance appearance);
}

internal sealed class AppearancePreference(IOptions<AppearanceOptions> options) : IAppearancePreference
{
    public Appearance Read(HttpContext context)
    {
        var name = options.Value.Cookie.Name!;
        return context.Request.Cookies.TryGetValue(name, out var value) && AppearanceNames.Parse(value) is { } parsed
            ? parsed
            : Appearance.System;
    }

    public void Write(HttpContext context, Appearance appearance)
    {
        var cookie = options.Value.Cookie;
        var name = cookie.Name!;
        if (appearance == Appearance.System)
        {
            context.Response.Cookies.Delete(name, cookie.Build(context));
            return;
        }

        context.Response.Cookies.Append(name, appearance.Name(), cookie.Build(context));
    }
}

public static class AppearanceModule
{
    public static IServiceCollection AddPlatformAppearance(this IServiceCollection services)
    {
        services.AddOptions<AppearanceOptions>();
        services.AddSingleton<IAppearancePreference, AppearancePreference>();
        return services;
    }

    /// <summary>
    /// 設定外觀的 POST 端點共用的處理：驗證 antiforgery、檢查值、寫 cookie。導回的位置由各面自己的檢查決定
    /// （只准回到那個面之下的站內路徑，防 open redirect）。
    /// 一般的表單送出（沒有 JavaScript）導回原頁；腳本用 <c>fetch</c> 送（<c>Accept: application/json</c>）則回 204，
    /// 畫面已經由腳本即時換好，不必再跟著轉址載入整頁。
    /// </summary>
    internal static async Task<IResult> SetAsync(
        HttpContext context, IAntiforgery antiforgery, IAppearancePreference preference, Func<string?, string> safeReturnUrl)
    {
        try
        {
            await antiforgery.ValidateRequestAsync(context);
        }
        catch (AntiforgeryValidationException)
        {
            return Results.BadRequest();
        }

        var form = await context.Request.ReadFormAsync(context.RequestAborted);
        if (AppearanceNames.Parse(form[AppearanceNames.Field]) is not { } appearance)
            return Results.BadRequest();

        preference.Write(context, appearance);
        return FromScript(context.Request) ? Results.NoContent() : Results.Redirect(safeReturnUrl(form["returnUrl"]));
    }

    /// <summary>腳本的 <c>fetch</c> 明說要 <c>application/json</c>；瀏覽器送出表單（含沒有 JavaScript）則沒有，照常導回。</summary>
    private static bool FromScript(HttpRequest request) =>
        request.GetTypedHeaders().Accept.Any(accept => accept.MediaType.Equals("application/json", StringComparison.OrdinalIgnoreCase));
}