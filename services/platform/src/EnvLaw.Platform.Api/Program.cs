using System.Reflection;

using EnvLaw.Platform.Api;
using EnvLaw.Platform.Data;
using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.Modules.Consult;
using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Ontology;
using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web;

using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

var builder = WebApplication.CreateBuilder(args);

// 建置時產生 OpenAPI 規格會以模擬伺服器執行本檔（進入組件為 GetDocument.Insider）：只建出端點、
// 不處理請求也不連線，故以占位值滿足啟動時的設定檢查。
var generatingOpenApi = Assembly.GetEntryAssembly()?.GetName().Name == "GetDocument.Insider";
if (generatingOpenApi)
{
    builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
    {
        ["ConnectionStrings:Platform"] = "Host=build-time",
        ["Ontology:InternalToken"] = "build-time",
        ["Consult:InternalToken"] = "build-time",
        ["Platform:Ui:Google:ClientId"] = "build-time",
        ["Platform:Ui:Google:ClientSecret"] = "build-time",
    });
}

// 設定只在各模組自己的 Options 類別裡讀（IPlatformOptions，啟動時驗證）；這裡不碰 IConfiguration。
builder.Services.AddPlatformOptions<PlatformHostOptions>();
builder.AddPlatformObservability();
builder.Services.AddPlatformOpenApi();
builder.Services.AddProblemDetails();
builder.Services.AddTenancyModule();
if (!generatingOpenApi)
{
    // Data Protection 啟動時就會預載金鑰環；模擬伺服器沒有資料庫，改用預設的暫時金鑰。
    builder.Services.AddPlatformDataProtection();
}

builder.Services.AddPlatformData();
builder.Services.AddApiAccessModule();
builder.Services.AddMeteringModule();
builder.Services.AddOntologyModule();
builder.Services.AddConsultModule();
builder.Services.AddPlatformWeb();

var app = builder.Build();

if (app.Services.GetRequiredService<IOptions<PlatformHostOptions>>().Value.ApplyMigrationsOnStartup)
{
    await using var scope = app.Services.CreateAsyncScope();
    await scope.ServiceProvider.GetRequiredService<TenancyDbContext>().Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<PlatformDbContext>().Database.MigrateAsync();
}

if (app.Environment.IsDevelopment())
{
    app.MapOpenApi();
}

app.UseHttpsRedirection();

// 順序很重要：API key 驗證會設定租戶 claim，Finbuckle 再據此解析租戶。
app.UseAuthentication();
app.UsePlatformWebAuthentication();
app.UseTenantTrace();
app.UseMultiTenant();
app.UseAuthorization();
app.UseRateLimiter();
app.UseAntiforgery();

app.MapPlatformHealth();

app.MapGroup("/admin")
    .RequireAuthorization(PlatformAuth.AdminPolicy)
    .MapTenancyAdminEndpoints()
    .MapApiAccessAdminEndpoints();

app.MapGroup("/v2")
    .RequireAuthorization(PlatformAuth.TenantPolicy)
    .RequireRateLimiting(PlatformAuth.PerTenantRateLimit)
    .MapTenancyEndpoints()
    .MapApiAccessEndpoints()
    .MapMeteringEndpoints()
    .MapOntologyEndpoints()
    .MapConsultEndpoints();

app.MapPlatformWeb();

await app.RunAsync();