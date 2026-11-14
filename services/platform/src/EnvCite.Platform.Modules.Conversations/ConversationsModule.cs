using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Modules.Conversations;

/// <summary>
/// 問答歷史的設定（<c>Platform:ConversationHistory</c>）。歷史一律存在，沒有開關；問題文字是客戶案件的內容，所以保存有期限。
/// </summary>
public sealed class ConversationHistoryOptions : ISectionOptions
{
    public static string SectionName => "Platform:ConversationHistory";

    /// <summary>超過這麼多天沒更新的對話會被刪除。</summary>
    [Range(1, 3650)]
    public int RetentionDays { get; set; } = 90;
}

[OptionsValidator]
public sealed partial class ConversationHistoryOptionsValidator : IValidateOptions<ConversationHistoryOptions>;

/// <summary>只讀設定（<see cref="ConversationHistoryOptions"/>）。不碰資料庫，所以互動式 circuit 可以安心注入。</summary>
internal sealed class ConversationHistorySettings(IOptions<ConversationHistoryOptions> options) : IConversationHistorySettings
{
    public int RetentionDays => options.Value.RetentionDays;
}

public static class ConversationsModule
{
    public static IServiceCollection AddConversationsModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddSectionOptions<ConversationHistoryOptions, ConversationHistoryOptionsValidator>();
        services.AddSingleton<IConversationHistorySettings, ConversationHistorySettings>();
        services.AddScoped<IConversationHistory, ConversationHistory>();
        // 無狀態的閘門：每個操作自己開租戶 scope，所以可以是 singleton，靜態頁與互動式 circuit 都能放心注入。
        services.AddSingleton<IAskService, AskService>();
        return services;
    }

    /// <summary>
    /// 定期刪除過期的對話。需要資料庫，所以和權益重新整理一樣不在產生 OpenAPI 的模擬 host 註冊。
    /// </summary>
    public static IServiceCollection AddConversationRetention(this IServiceCollection services)
    {
        services.AddSingleton<ConversationRetention>();
        services.AddHostedService(provider => provider.GetRequiredService<ConversationRetention>());
        return services;
    }
}