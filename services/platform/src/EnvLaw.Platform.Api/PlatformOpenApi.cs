using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.OpenApi;
using Microsoft.OpenApi;

namespace EnvLaw.Platform.Api;

/// <summary>對外 REST 的 OpenAPI 規格：只收租戶可呼叫的 <c>/v2</c>，營運者的 <c>/admin</c> 不列入。</summary>
internal static class PlatformOpenApi
{
    public const string ApiKeyScheme = "ApiKey";

    public static IServiceCollection AddPlatformOpenApi(this IServiceCollection services) =>
        services.AddOpenApi("v2", options =>
        {
            options.ShouldInclude = description => description.RelativePath?.StartsWith("v2/", StringComparison.Ordinal) == true;
            options.AddDocumentTransformer((document, _, _) =>
            {
                document.Info = new OpenApiInfo
                {
                    Title = "env-law API",
                    Version = "v2",
                    Description = "環境法規 API。以租戶的 API key 呼叫（`Authorization: Bearer sk_live_…`）。"
                        + "拒答是結果、不是錯誤：`POST /v2/ground` 有法源與拒答都回 200，以 `outcome` 區分。",
                };
                document.Components ??= new OpenApiComponents();
                document.Components.SecuritySchemes = new Dictionary<string, IOpenApiSecurityScheme>
                {
                    [ApiKeyScheme] = new OpenApiSecurityScheme
                    {
                        Type = SecuritySchemeType.Http,
                        Scheme = "bearer",
                        BearerFormat = "API key",
                        Description = "租戶的 API key，由營運者核發；只在核發時顯示一次。",
                    },
                };
                document.Security = [new OpenApiSecurityRequirement { [new OpenApiSecuritySchemeReference(ApiKeyScheme, document)] = [] }];
                return Task.CompletedTask;
            });
            // 每個 /v2 端點都經 API key 驗證與逐租戶限流，故兩種回應逐一列出。
            options.AddOperationTransformer((operation, context, _) =>
            {
                operation.Responses ??= [];
                operation.Responses["401"] = new OpenApiResponse { Description = "API key 缺少、無效、已過期或已撤銷。" };
                operation.Responses["429"] = new OpenApiResponse { Description = "超過本租戶方案的速率限制。" };
                // 資料端點（PaidPolicy）另外要求租戶有方案；管理端點不要求，所以不列 402。
                if (context.Description.ActionDescriptor.EndpointMetadata
                    .OfType<IAuthorizeData>().Any(a => a.Policy == PlatformAuth.PaidPolicy))
                {
                    operation.Responses["402"] = new OpenApiResponse { Description = "本租戶目前沒有有效的方案（完全付費制）。請聯絡業務啟用。" };
                }

                return Task.CompletedTask;
            });
        });
}