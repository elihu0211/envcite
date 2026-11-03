using System.ClientModel;

using EnvCite.Consult.V2;
using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

using OpenAI;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>envcite-consult 的連線設定（<c>Consult</c> 區段；叢集內為 <c>dns:///envcite-consult:50052</c>）。</summary>
public sealed class ConsultOptions : InternalGrpcClientOptions, ISectionOptions
{
    public static string SectionName => "Consult";
}

/// <summary>
/// 回答用的模型（<c>Platform:Answer</c> 區段）：任何 OpenAI 相容的端點。預設關閉；關閉時入口只列條文，
/// 不呼叫模型。<c>ApiKey</c> 是機密（<c>ENVCITE_Platform__Answer__ApiKey</c>），不進設定檔。
/// </summary>
public sealed class AnswerOptions : ISectionOptions
{
    public static string SectionName => "Platform:Answer";

    public bool Enabled { get; set; }

    /// <summary>OpenAI 相容端點，例如 <c>https://api.ithu.tw/v1</c>。</summary>
    public Uri? Endpoint { get; set; }

    public string ApiKey { get; set; } = string.Empty;

    /// <summary>模型 ID。端點上的名稱可能是別名，換模型只改這裡。</summary>
    public string Model { get; set; } = "mistral-small-4";

    /// <summary>一次回答（含模型）的時間上限。</summary>
    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(60);

    public int MaxOutputTokens { get; set; } = 800;
}

internal sealed class AnswerOptionsValidator : IValidateOptions<AnswerOptions>
{
    public ValidateOptionsResult Validate(string? name, AnswerOptions options)
    {
        if (!options.Enabled)
        {
            return ValidateOptionsResult.Success;
        }

        var errors = new List<string>();
        if (options.Endpoint is not { IsAbsoluteUri: true })
        {
            errors.Add("Platform:Answer:Endpoint must be an absolute URL when the answer service is enabled.");
        }

        if (string.IsNullOrWhiteSpace(options.ApiKey))
        {
            errors.Add("Platform:Answer:ApiKey is required when the answer service is enabled.");
        }

        if (string.IsNullOrWhiteSpace(options.Model))
        {
            errors.Add("Platform:Answer:Model is required when the answer service is enabled.");
        }

        if (options.Timeout <= TimeSpan.Zero)
        {
            errors.Add("Platform:Answer:Timeout must be positive.");
        }

        if (options.MaxOutputTokens is < 1 or > 8000)
        {
            errors.Add("Platform:Answer:MaxOutputTokens must be between 1 and 8000.");
        }

        return errors.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(errors);
    }
}

public static class ConsultModule
{
    public static IServiceCollection AddConsultModule(this IServiceCollection services)
    {
        services.AddPlatformGrpcClient<ConsultationService.ConsultationServiceClient, ConsultOptions>();
        services.AddSectionOptions<AnswerOptions, AnswerOptionsValidator>();
        // 只在有人要用時才建立（關閉時沒有端點與金鑰可建）；測試以自己的 IChatClient 取代。
        services.TryAddSingleton<IChatClient>(provider =>
        {
            var answer = provider.GetRequiredService<IOptions<AnswerOptions>>().Value;
            return new OpenAIClient(new ApiKeyCredential(answer.ApiKey), new OpenAIClientOptions { Endpoint = answer.Endpoint })
                .GetChatClient(answer.Model)
                .AsIChatClient();
        });
        services.AddScoped<IGroundedAnswerer, GroundedAnswerer>();
        return services;
    }
}