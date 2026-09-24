using EnvLaw.Platform.SharedKernel;

using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace EnvLaw.Platform.Modules.Tenancy;

/// <summary>EF Core 工具與遷移 bundle 建立 context 的途徑，不經應用程式的 host（見 <c>PlatformDbContextDesignTimeFactory</c>）。</summary>
public sealed class TenancyDbContextDesignTimeFactory : IDesignTimeDbContextFactory<TenancyDbContext>
{
    public TenancyDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<TenancyDbContext>();
        TenancyModule.Configure(options, DesignTimeConnection.Get());
        return new TenancyDbContext(options.Options);
    }
}