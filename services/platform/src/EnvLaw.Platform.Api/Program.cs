using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Ontology;
using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

var connectionString = builder.Configuration.GetConnectionString("Platform")
    ?? throw new InvalidOperationException("Connection string 'Platform' is not configured.");
var platformConfig = builder.Configuration.GetSection("Platform");

builder.Services.AddOpenApi();
builder.Services.AddProblemDetails();
builder.Services.AddTenancyModule(connectionString);
builder.Services.AddApiAccessModule(connectionString, platformConfig);
builder.Services.AddMeteringModule(connectionString);
builder.Services.AddOntologyModule(builder.Configuration.GetSection("Ontology"));

var app = builder.Build();

if (platformConfig.GetValue<bool>("ApplyMigrationsOnStartup"))
{
    await using var scope = app.Services.CreateAsyncScope();
    await scope.ServiceProvider.GetRequiredService<TenancyDbContext>().Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<ApiAccessDbContext>().Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<MeteringDbContext>().Database.MigrateAsync();
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
    .MapOntologyEndpoints();

await app.RunAsync();