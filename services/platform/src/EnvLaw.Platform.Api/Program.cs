using System.Reflection;

using EnvLaw.Platform.Api;
using EnvLaw.Platform.Data;
using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.Modules.Consult;
using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Ontology;
using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// 建置時產生 OpenAPI 規格會以模擬伺服器執行本檔（進入組件為 GetDocument.Insider）：只建出端點、
// 不處理請求也不連線，故以占位值滿足啟動時的設定檢查。
if (Assembly.GetEntryAssembly()?.GetName().Name == "GetDocument.Insider")
{
    builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
    {
        ["ConnectionStrings:Platform"] = "Host=build-time",
        ["Ontology:InternalToken"] = "build-time",
        ["Consult:InternalToken"] = "build-time",
    });
}

var connectionString = builder.Configuration.GetConnectionString("Platform")
    ?? throw new InvalidOperationException("Connection string 'Platform' is not configured.");
var platformConfig = builder.Configuration.GetSection("Platform");

builder.Services.AddPlatformOpenApi();
builder.Services.AddProblemDetails();
builder.Services.AddTenancyModule(connectionString);
builder.Services.AddPlatformData(connectionString);
builder.Services.AddApiAccessModule(platformConfig);
builder.Services.AddMeteringModule();
builder.Services.AddOntologyModule(builder.Configuration.GetSection("Ontology"));
builder.Services.AddConsultModule(builder.Configuration.GetSection("Consult"));

var app = builder.Build();

if (platformConfig.GetValue<bool>("ApplyMigrationsOnStartup"))
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
app.UseMultiTenant();
app.UseAuthorization();
app.UseRateLimiter();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }))
    .WithName("Health");

app.MapGroup("/admin")
    .RequireAuthorization(PlatformAuth.AdminPolicy)
    .MapTenancyAdminEndpoints()
    .MapApiAccessAdminEndpoints();

app.MapGroup("/v1")
    .RequireAuthorization(PlatformAuth.TenantPolicy)
    .RequireRateLimiting(PlatformAuth.PerTenantRateLimit)
    .MapTenancyEndpoints()
    .MapApiAccessEndpoints()
    .MapMeteringEndpoints()
    .MapOntologyEndpoints()
    .MapConsultEndpoints();

await app.RunAsync();