using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 在 <c>/v2</c> 端點上強制檢查租戶是否啟用指定的領域模組；
/// 在方案檢查（402）之後執行，未啟用時回傳 403（帶 <see cref="OutcomeCodes.ModuleDisabled"/>）。
/// </summary>
public static class ModuleEndpointFilterExtensions
{
    public static RouteHandlerBuilder RequireModule(this RouteHandlerBuilder builder, string moduleKey) =>
        builder.AddEndpointFilter(async (context, next) =>
        {
            var tenant = context.HttpContext.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value;
            var modules = context.HttpContext.RequestServices.GetRequiredService<ITenantModules>();
            if (string.IsNullOrEmpty(tenant) || !modules.IsEnabled(tenant, moduleKey))
            {
                return Results.Problem(
                    statusCode: StatusCodes.Status403Forbidden,
                    title: "Module disabled",
                    detail: OutcomeCodes.Describe(OutcomeCodes.ModuleDisabled, [moduleKey]),
                    extensions: new Dictionary<string, object?>
                    {
                        ["code"] = OutcomeCodes.ModuleDisabled,
                        ["module"] = moduleKey,
                    });
            }

            return await next(context);
        });
}