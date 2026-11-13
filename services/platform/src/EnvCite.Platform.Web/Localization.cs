using System.Globalization;

using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Headers;
using Microsoft.AspNetCore.Localization;

namespace EnvCite.Platform.Web;

/// <summary>
/// 介面語言：繁體中文（預設）與英文，用 ASP.NET Core 內建的 request localization。
/// 順序是使用者自己選的（cookie）、瀏覽器的 <c>Accept-Language</c>、預設 zh-TW；網址不放語言前綴（後台與入口都要登入，不需要 SEO）。
/// 只套用在後台、入口與 Blazor 連線：<c>/v2</c> 的行為不隨語言變。
/// 語言與時區無關：時間照舊顯示臺北時間。
/// </summary>
public static class Localization
{
    public const string Default = "zh-TW";

    /// <summary>支援的語言與它們自己的名稱（語言選項顯示各語言的自稱，不翻譯）。</summary>
    public static IReadOnlyList<(string Code, string Name)> Languages { get; } = [("zh-TW", "繁體中文"), ("en", "English")];

    public static bool IsSupported(string? culture) => Languages.Any(l => l.Code == culture);

    public static IServiceCollection AddPlatformLocalization(this IServiceCollection services)
    {
        services.AddLocalization();
        services.Configure<RequestLocalizationOptions>(options =>
        {
            var cultures = Languages.Select(l => new CultureInfo(l.Code)).ToList();
            options.DefaultRequestCulture = new RequestCulture(Default);
            options.SupportedCultures = cultures;
            options.SupportedUICultures = cultures;
            // 不認網址的 ?culture=：換語言只經過 POST 端點（antiforgery），連結不能替人換語言。
            options.RequestCultureProviders = [new CookieRequestCultureProvider(), new CustomRequestCultureProvider(AcceptLanguage)];
        });
        return services;
    }

    /// <summary>接在認證之前（語言不需要知道是誰、哪個租戶），只對 UI 的路徑生效。</summary>
    public static IApplicationBuilder UsePlatformLocalization(this IApplicationBuilder app) =>
        app.UseWhen(
            context => IsUiPath(context.Request.Path),
            branch => branch.UseRequestLocalization());

    private static bool IsUiPath(PathString path) =>
        path.StartsWithSegments("/ui") || path.StartsWithSegments(PortalPaths.Prefix)
        || path.StartsWithSegments("/_blazor") || path.StartsWithSegments(WebModule.DesignGalleryPath)
        || path.StartsWithSegments(WebModule.NotFoundPath);

    /// <summary>
    /// 依品質值由高到低取第一個認得的語言：任何 zh 開頭（zh-TW、zh-Hant、zh-HK…）都用繁體中文，en 開頭用英文，
    /// 其餘略過。內建的 Accept-Language 只認完全相符的文化名稱，會把 zh-Hant-TW 判成不支援。
    /// </summary>
    private static Task<ProviderCultureResult?> AcceptLanguage(HttpContext context)
    {
        var languages = new RequestHeaders(context.Request.Headers).AcceptLanguage;
        foreach (var language in languages.OrderByDescending(l => l.Quality ?? 1.0))
        {
            var tag = language.Value.Value;
            if (tag is null)
                continue;

            if (tag.StartsWith("zh", StringComparison.OrdinalIgnoreCase))
                return Task.FromResult<ProviderCultureResult?>(new ProviderCultureResult(Default));

            if (tag.StartsWith("en", StringComparison.OrdinalIgnoreCase))
                return Task.FromResult<ProviderCultureResult?>(new ProviderCultureResult("en"));
        }

        return Task.FromResult<ProviderCultureResult?>(null);
    }

    /// <summary>
    /// 換語言的 POST 端點共用的處理：驗證 antiforgery、寫 cookie、導回原頁。導回的位置由各面自己的檢查決定
    /// （只准回到那個面之下的站內路徑，防 open redirect）。
    /// </summary>
    internal static async Task<IResult> SwitchAsync(HttpContext context, IAntiforgery antiforgery, Func<string?, string> safeReturnUrl)
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
        string? culture = form["culture"];
        if (!IsSupported(culture))
            return Results.BadRequest();

        // 只是偏好，不認證任何東西；HttpOnly 因為只有伺服器讀。一年後過期。
        context.Response.Cookies.Append(
            CookieRequestCultureProvider.DefaultCookieName,
            CookieRequestCultureProvider.MakeCookieValue(new RequestCulture(culture!)),
            new CookieOptions
            {
                Path = "/",
                MaxAge = TimeSpan.FromDays(365),
                HttpOnly = true,
                IsEssential = true,
                SameSite = SameSiteMode.Lax,
                Secure = context.Request.IsHttps,
            });
        return Results.Redirect(safeReturnUrl(form["returnUrl"]));
    }
}