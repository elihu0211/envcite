using System.Reflection;

using EnvCite.Platform.Api;
using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Audit;
using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Conversations;
using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;
using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web;

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
    builder.Services.AddEntitlementRefresh();
    builder.Services.AddConversationRetention();
}

builder.Services.AddPlatformData();
builder.Services.AddApiAccessModule();
builder.Services.AddAuditModule();
builder.Services.AddMembersModule();
builder.Services.AddConversationsModule();
builder.Services.AddSingleton<ITenantDirectory, TenantDirectory>();
builder.Services.AddMeteringModule();
builder.Services.AddOntologyModule();
builder.Services.AddConsultModule();
builder.Services.AddPlatformWeb();

var app = builder.Build();

var hostOptions = app.Services.GetRequiredService<IOptions<PlatformHostOptions>>().Value;

// 產生 OpenAPI 規格的模擬 host 沒有資料庫；在 Development 環境下重建（wt platform 每次改程式後）也會走到這裡。
if (!generatingOpenApi && hostOptions.ApplyMigrationsOnStartup)
{
    await using var scope = app.Services.CreateAsyncScope();
    await scope.ServiceProvider.GetRequiredService<TenancyDbContext>().Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<PlatformDbContext>().Database.MigrateAsync();
}

// 本機的第一組資料：只在 Development（設定開著時）；一次性密碼只印在終端機，不進日誌。
if (!generatingOpenApi && app.Environment.IsDevelopment() && hostOptions.SeedDevelopmentData)
{
    if (await DevelopmentSeed.RunAsync(app.Services, CancellationToken.None) is { } seeded)
    {
        Console.WriteLine(DevelopmentSeed.Describe(seeded));
    }

    if (await DevelopmentSeed.RunOperatorAsync(app.Services, CancellationToken.None) is { } seededOperator)
    {
        Console.WriteLine(DevelopmentSeed.Describe(seededOperator));
    }
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
app.UsePlatformPortalAuthentication();
app.UseAuthorization();
app.UseRateLimiter();
app.UseAntiforgery();

app.MapPlatformHealth();

app.MapGroup("/admin")
    .RequireAuthorization(PlatformAuth.AdminPolicy)
    .MapTenancyAdminEndpoints()
    .MapApiAccessAdminEndpoints()
    .MapAuditAdminEndpoints()
    .MapMembersAdminEndpoints()
    .MapOperatorsAdminEndpoints();

var v2 = app.MapGroup("/v2")
    .RequireAuthorization(PlatformAuth.TenantPolicy)
    .RequireRateLimiting(PlatformAuth.PerTenantRateLimit);

// 管理端點：沒有方案的租戶也要能看自己是誰、看用量、撤銷被盜的 key。
v2.MapTenancyEndpoints()
    .MapApiAccessEndpoints()
    .MapMeteringEndpoints();

// 資料端點：完全付費制，租戶要有方案，否則 402。
v2.MapGroup(string.Empty)
    .RequireAuthorization(PlatformAuth.PaidPolicy)
    .MapOntologyEndpoints()
    .MapReasoningEndpoints()
    .MapConsultEndpoints();



app.MapPlatformWeb();

await app.RunAsync();