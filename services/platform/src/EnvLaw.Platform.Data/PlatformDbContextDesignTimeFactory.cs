using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;

using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace EnvLaw.Platform.Data;

/// <summary>
/// EF Core 工具與遷移 bundle 建立 context 的途徑，不經應用程式的 host：否則 bundle 執行時
/// 會跑一遍 Program.cs，連遷移用不到的內部服務設定都得提供。
/// 連線字串與應用程式同一個鍵（<c>ConnectionStrings__Platform</c>）；bundle 的
/// <c>--connection</c> 可覆寫。
/// </summary>
public sealed class PlatformDbContextDesignTimeFactory : IDesignTimeDbContextFactory<PlatformDbContext>
{
    public PlatformDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<PlatformDbContext>();
        PlatformDataModule.Configure(options, DesignTimeConnection.Get());

        // 遷移只需要模型，不查詢資料；租戶只是 MultiTenantDbContext 建構時的必要參數。
        return MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(
            new TenantInfo { Id = "design-time", Identifier = "design-time" }, options.Options);
    }
}