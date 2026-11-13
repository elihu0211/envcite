using System.Diagnostics;

using EnvCite.Platform.Modules.Consult;

using Microsoft.Extensions.AI;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 模型呼叫有追蹤（部署者看得到延遲與 token），但提示詞與回應內容永不進追蹤：問題文字是客戶案情。
/// 對的是 <see cref="AnswerTelemetry.Wrap"/> 本身，不經主機，所以不受其他測試的主機設定影響。
/// </summary>
public sealed class AnswerTelemetryTests
{
    private const string Question = "某客戶新北某工廠的排放爭議";

    private const string Reply = "在指定清除地區內不得燃燒 [L1]。";

    private sealed class FakeChat : IChatClient
    {
        public Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, Reply))
            {
                Usage = new UsageDetails { InputTokenCount = 7, OutputTokenCount = 3 },
            });

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    private static async Task<List<Activity>> TraceOneCallAsync()
    {
        var activities = new List<Activity>();
        using var listener = new ActivityListener
        {
            ShouldListenTo = source => source.Name == AnswerTelemetry.SourceName,
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = activity =>
            {
                lock (activities)
                    activities.Add(activity);
            },
        };
        ActivitySource.AddActivityListener(listener);

        using var client = AnswerTelemetry.Wrap(new FakeChat());
        await client.GetResponseAsync([new ChatMessage(ChatRole.User, Question)], cancellationToken: TestContext.Current.CancellationToken);

        lock (activities)
            return [.. activities];
    }

    private static IEnumerable<string> Texts(Activity activity) =>
        activity.TagObjects.Select(t => t.Value?.ToString() ?? string.Empty)
            .Concat(activity.Events.SelectMany(e => e.Tags.Select(t => t.Value?.ToString() ?? string.Empty)))
            .Append(activity.DisplayName)
            .Append(activity.StatusDescription ?? string.Empty);

    [Fact]
    public async Task TheModelCallIsTracedWithItsTokenCountsAndNothingElseAboutItsContent()
    {
        var activities = await TraceOneCallAsync();

        var activity = Assert.Single(activities);
        Assert.Contains(activity.TagObjects, t => t.Key == "gen_ai.usage.input_tokens" && Equals(t.Value, 7));
        Assert.DoesNotContain(activities.SelectMany(Texts), text => text.Contains("排放爭議", StringComparison.Ordinal));
        Assert.DoesNotContain(activities.SelectMany(Texts), text => text.Contains("不得燃燒", StringComparison.Ordinal));
    }

    // 框架的預設會讀這個環境變數；我們明確關掉，環境一開也不能把提示詞帶進追蹤。
    [Fact]
    public async Task TheCaptureSwitchInTheEnvironmentCannotTurnContentCaptureOn()
    {
        const string Switch = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT";
        var before = Environment.GetEnvironmentVariable(Switch);
        Environment.SetEnvironmentVariable(Switch, "true");
        try
        {
            var activities = await TraceOneCallAsync();

            Assert.NotEmpty(activities);
            Assert.DoesNotContain(activities.SelectMany(Texts), text => text.Contains("排放爭議", StringComparison.Ordinal));
            Assert.DoesNotContain(activities.SelectMany(Texts), text => text.Contains("不得燃燒", StringComparison.Ordinal));
        }
        finally
        {
            Environment.SetEnvironmentVariable(Switch, before);
        }
    }
}