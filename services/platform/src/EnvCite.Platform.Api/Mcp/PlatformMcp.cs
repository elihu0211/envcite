using System.Reflection;

using EnvCite.Platform.Modules.Consult.Mcp;
using EnvCite.Platform.Modules.Ontology.Mcp;
using EnvCite.ServiceDefaults;

using ModelContextProtocol.AspNetCore;

namespace EnvCite.Platform.Api.Mcp;

/// <summary>MCP 伺服器的設定（<c>Platform:Mcp</c> 區段）。預設關閉：沒開就不掛，<c>/v2/mcp</c> 回 404。Development 在 appsettings 裡開著。</summary>
public sealed class McpOptions : ISectionOptions
{
    public static string SectionName => "Platform:Mcp";

    public bool Enabled { get; set; }

    /// <summary>
    /// 允許的瀏覽器來源（例如 <c>https://inspector.example.com</c>）。MCP 的 HTTP 傳輸要檢查 <c>Origin</c>（防 DNS rebinding）：
    /// 帶了 <c>Origin</c> 而不在這份清單裡的請求回 403。CLI 與桌面用戶端不送 <c>Origin</c>，不受影響。
    /// </summary>
    public string[] AllowedOrigins { get; set; } = [];
}

internal sealed class McpOptionsValidator : IValidateOptions<McpOptions>
{
    public ValidateOptionsResult Validate(string? name, McpOptions options)
    {
        var invalid = options.AllowedOrigins.Where(origin =>
            !Uri.TryCreate(origin, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https")
            || uri.PathAndQuery != "/"
            || origin.EndsWith('/')).ToList();

        return invalid.Count == 0
            ? ValidateOptionsResult.Success
            : ValidateOptionsResult.Fail($"Platform:Mcp:AllowedOrigins must be origins such as https://host (no path, no trailing slash): {string.Join(", ", invalid)}.");
    }
}

/// <summary>
/// 給 AI 代理用的 MCP 入口（Streamable HTTP，<c>/v2/mcp</c>）：與 REST 的資料端點是同一批能力，工具包在同一份實作外面。
/// 它掛在 <c>/v2</c> 的資料群組裡，所以 API key、方案（沒有方案回 402）與每租戶限流與 REST 完全一樣，計量也一樣（成功的呼叫記一次）。
/// <b>無狀態</b>：每個請求獨立，沒有 session 放在某一個副本的記憶體裡，多副本之間不需要連線黏著。
/// 身分驗證用既有的 API key（<c>Authorization: Bearer</c>）；不實作 OAuth 授權伺服器。
/// </summary>
public static class PlatformMcp
{
    public const string Path = "mcp";

    public static IServiceCollection AddPlatformMcp(this IServiceCollection services)
    {
        services.AddSectionOptions<McpOptions, McpOptionsValidator>();
        services.AddMcpServer(server =>
            {
                server.ServerInfo = new()
                {
                    Name = "envcite",
                    Title = "環境知識",
                    Version = typeof(PlatformMcp).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0",
                };
                server.ServerInstructions =
                    "臺灣環境法規的有依據查詢。回答法規問題時，先用 ground_question 取得法源，再只根據法源作答、以代號標明出處；"
                    + "只想找條文用 search_articles；要看某部法規與各條用 get_law、list_articles 或讀資源 envcite://laws/{jurisdiction}/{idScheme}/{localId}。"
                    + "所有查詢都可以指定 pointInTime（YYYY-MM-DD）查某一天施行的條文，省略表示現行。拒答與空結果是結果，不是錯誤：不要因此用自己的知識補。"
                    + "只涵蓋中央法規；地方自治法規查不到不代表沒有規定。結果是依條文整理的資訊，不是法律意見。";
            })
            .WithHttpTransport(http => http.SessionMode = HttpServerSessionMode.Stateless)
            .WithOntologyTools()
            .WithConsultTools();
        return services;
    }

    /// <summary>
    /// 檢查 <c>Origin</c>（MCP 規範要求，防 DNS rebinding）：帶了 <c>Origin</c> 而不在允許清單裡就 403。
    /// 接在驗證之前：沒有通過來源檢查的請求，連身分都不必查。
    /// </summary>
    public static IApplicationBuilder UsePlatformMcpOriginCheck(this IApplicationBuilder app) =>
        app.Use(async (context, next) =>
        {
            if (context.Request.Path.StartsWithSegments($"{ApiPaths.V2}/{Path}")
                && context.Request.Headers.Origin.ToString() is { Length: > 0 } origin
                && !context.RequestServices.GetRequiredService<IOptions<McpOptions>>().Value.AllowedOrigins.Contains(origin, StringComparer.OrdinalIgnoreCase))
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                return;
            }

            await next(context);
        });

    /// <summary>掛在 <c>/v2</c> 的資料群組（API key、方案、限流）裡；沒開就不掛。不進 OpenAPI 規格：它是另一個協定，不是 REST 端點。</summary>
    public static RouteGroupBuilder MapPlatformMcp(this RouteGroupBuilder paid, bool enabled)
    {
        if (enabled)
            paid.MapMcp(Path).ExcludeFromDescription();

        return paid;
    }
}