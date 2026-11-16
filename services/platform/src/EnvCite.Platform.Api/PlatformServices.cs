using EnvCite.Platform.Api.Mcp;
using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Audit;
using EnvCite.Platform.Modules.Canon;
using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Conversations;
using EnvCite.Platform.Modules.Mail;
using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.Web;
using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Api;

/// <summary>組合根的服務註冊：把各模組接起來的唯一地方。模組之間不互相參考，所以只有這裡看得到全部。</summary>
internal static class PlatformServices
{
    /// <param name="generatingOpenApi">建置時產生 OpenAPI 規格的模擬伺服器：只建出端點、不連線（見 <see cref="PlatformHost"/>）。</param>
    public static WebApplicationBuilder AddPlatform(this WebApplicationBuilder builder, bool generatingOpenApi)
    {
        var services = builder.Services;

        // 設定只在各模組的 Options 類別讀取（ISectionOptions，啟動時驗證），這裡不碰 IConfiguration。
        services.AddSectionOptions<PlatformHostOptions>();
        builder.AddPlatformObservability();
        services.AddPlatformOpenApi();
        services.AddPlatformProblemDetails();
        services.AddTenancyModule();
        if (!generatingOpenApi)
        {
            // Data Protection 啟動時就會預載金鑰環；模擬伺服器沒有資料庫，改用預設的暫時金鑰。
            services.AddPlatformDataProtection(builder.Configuration);
            services.AddEntitlementRefresh();
            services.AddConversationRetention();
            services.AddIdempotencyRetention();
        }

        services.AddPlatformData();
        services.AddApiAccessModule();
        services.AddAuditModule();

        // 寄信（傳輸、版型、文案、系統名稱）集中在這一處註冊，成員模組與命令列共用。
        services.AddMailModule();
        services.AddMembersModule();
        services.AddConversationsModule();
        services.AddSingleton<ITenantDirectory, TenantDirectory>();
        services.AddMeteringModule();
        services.AddCanonModule();
        services.AddConsultModule();
        services.AddPlatformWeb();
        services.AddPlatformMcp();
        services.AddPlatformLocalization();
        return builder;
    }
}