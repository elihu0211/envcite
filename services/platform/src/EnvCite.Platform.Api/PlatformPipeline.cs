using EnvCite.Platform.Api.Mcp;
using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Canon;
using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Members.DirectorySync;
using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.Web;

using Finbuckle.MultiTenant.AspNetCore.Extensions;

namespace EnvCite.Platform.Api;

/// <summary>請求管線與端點的接線。中介層的順序是承重的，所以整條放在同一個方法裡，順序的理由寫在各行旁。</summary>
internal static class PlatformPipeline
{
    public static WebApplication UsePlatformPipeline(this WebApplication app)
    {
        if (app.Environment.IsDevelopment())
            app.MapOpenApi();

        // 最外層：之後任何中介層或端點的未捕捉例外都收在這裡。Development 保留框架的開發者例外頁（含堆疊）。
        if (!app.Environment.IsDevelopment())
            app.UseExceptionHandler();

        app.UseHttpsRedirection();

        // 順序很重要：API key 驗證會設定租戶 claim，Finbuckle 再據此解析租戶。
        app.UsePlatformNotFoundPage();

        // 明確呼叫 UseRouting，且要在找不到頁面的中介層之後（原因見 UsePlatformNotFoundPage）；端點由 WebApplication 在最後接上。
        app.UseRouting();
        app.UsePlatformLocalization();
        app.UsePlatformMcpOriginCheck();
        app.UseAuthentication();
        app.UsePlatformWebAuthentication();
        app.UseTenantTrace();
        app.UseMultiTenant();
        app.UsePlatformPortalAuthentication();
        app.UseAuthorization();
        app.UseRateLimiter();
        // 兜底的請求逾時（只有掛了政策的端點，即 /v2）；要在路由之後才看得到端點的政策。
        app.UseRequestTimeouts();
        app.UseAntiforgery();
        return app;
    }

    public static WebApplication MapPlatformEndpoints(this WebApplication app)
    {
        app.MapPlatformHealth();

        var v2 = app.MapGroup(ApiPaths.V2)
            .RequireAuthorization(PlatformAuth.TenantPolicy)
            .RequireRateLimiting(PlatformAuth.PerTenantRateLimit);
        v2.WithRequestTimeout(PlatformRequestTimeouts.ApiPolicy);

        // 管理端點：沒有方案的租戶也要能看自己是誰、看用量、撤銷被盜的 key。
        v2.MapTenancyEndpoints()
            .MapApiAccessEndpoints()
            .MapMeteringEndpoints();

        // 資料端點：完全付費制，租戶要有方案，否則 402。
        var paid = v2.MapGroup(string.Empty)
            .RequireAuthorization(PlatformAuth.PaidPolicy);
        paid.MapCanonEndpoints()
            .MapReasoningEndpoints()
            .MapConsultEndpoints();

        // 給 AI 代理的 MCP 入口：同一批資料能力，同一個群組的 API key、方案與限流。預設關閉。
        paid.MapPlatformMcp(app.Services.GetRequiredService<IOptions<McpOptions>>().Value.Enabled);

        // 目錄同步（SCIM）：不在 /v2 之下，沒有方案與計量；部署者沒開功能面就不掛。
        app.MapDirectoryScim();

        app.MapPlatformWeb();
        return app;
    }
}