using EnvLaw.Platform.Modules.ApiAccess;
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

var app = builder.Build();

if (platformConfig.GetValue<bool>("ApplyMigrationsOnStartup"))
{
    await using var scope = app.Services.CreateAsyncScope();
    await scope.ServiceProvider.GetRequiredService<TenancyDbContext>().Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<ApiAccessDbContext>().Database.MigrateAsync();
}

if (app.Environment.IsDevelopment())
{
    app.MapOpenApi();
}

app.UseHttpsRedirection();

// Order matters: API key auth sets the tenant claim that Finbuckle resolves the tenant from.
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
    .MapApiAccessEndpoints();

await app.RunAsync();

/// <summary>Entry point marker for WebApplicationFactory-based tests.</summary>
public partial class Program;