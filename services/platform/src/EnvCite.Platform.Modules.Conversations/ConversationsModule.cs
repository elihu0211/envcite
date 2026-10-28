using System.ComponentModel.DataAnnotations;

using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Conversations;

/// <summary>
/// 問答歷史。問題文字是客戶案件的內容，所以預設關閉；開啟是部署者的決定（<c>Platform:ConversationHistory</c>）。
/// </summary>
public sealed class ConversationHistoryOptions : IPlatformOptions
{
    public static string SectionName => "Platform:ConversationHistory";

    public bool Enabled { get; set; }

    /// <summary>超過這麼多天沒更新的對話會被刪除。</summary>
    [Range(1, 3650)]
    public int RetentionDays { get; set; } = 90;
}

[OptionsValidator]
public sealed partial class ConversationHistoryOptionsValidator : IValidateOptions<ConversationHistoryOptions>;

public static class ConversationsModule
{
    public static IServiceCollection AddConversationsModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddPlatformOptions<ConversationHistoryOptions, ConversationHistoryOptionsValidator>();
        services.AddScoped<IConversationHistory, ConversationHistory>();
        return services;
    }

    /// <summary>
    /// 定期刪除過期的對話。需要資料庫，所以和權益重新整理一樣不在產生 OpenAPI 的模擬 host 註冊。
    /// 沒開啟問答歷史時什麼也不做。
    /// </summary>
    public static IServiceCollection AddConversationRetention(this IServiceCollection services)
    {
        services.AddSingleton<ConversationRetention>();
        services.AddHostedService(provider => provider.GetRequiredService<ConversationRetention>());
        return services;
    }
}