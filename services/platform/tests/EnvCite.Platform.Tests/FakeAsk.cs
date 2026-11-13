using System.Security.Claims;

namespace EnvCite.Platform.Tests;

/// <summary>可程式化的 <see cref="IAskService"/>：測 <c>ChatSession</c> 與入口元件時，把領域動作換成它。</summary>
internal sealed class FakeAsk : IAskService
{
    public AskCapabilities Capabilities { get; set; } = new(HasPlan: true, AiAnswer: true);

    public static AnswerSource Source { get; } = new("L1", "https://example.test/law/32", "空氣污染防制法 第 32 條", "在指定清除地區內，不得從事燃燒行為。");

    public AskResult Result { get; set; } = new(AskMode.Answer, AskStatus.Ok, Answer: new GroundedAnswer("答 [L1]", [Source]));

    public Exception? Throws { get; set; }

    public TaskCompletionSource? Gate { get; set; }

    /// <summary>在 <see cref="Gate"/> 之前回報的步驟：測「等結果時說明進行到哪一步」。</summary>
    public List<AnswerProgress> StagesBeforeGate { get; } = [];

    public Guid? RecordedAs { get; set; } = Guid.NewGuid();

    public ConversationDetail? Detail { get; set; }

    public List<(string? Question, string? AsOf)> Asked { get; } = [];

    public List<(Guid? Conversation, string Question, string Answer)> Recorded { get; } = [];

    public TaskCompletionSource? CapabilitiesGate { get; set; }

    public async Task<AskCapabilities> CapabilitiesAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (CapabilitiesGate is not null)
            await CapabilitiesGate.Task;

        return Capabilities;
    }

    public async Task<AskResult> AskAsync(
        ClaimsPrincipal user, string? question, string? asOf, IProgress<AnswerProgress>? progress, CancellationToken cancellationToken)
    {
        Asked.Add((question, asOf));
        foreach (var stage in StagesBeforeGate)
        {
            progress?.Report(stage);
        }

        if (Gate is not null)
            await Gate.Task;

        return Throws is null ? Result : throw Throws;
    }

    public Task<IReadOnlyList<ConversationSummary>> RecentAsync(ClaimsPrincipal user, int limit, CancellationToken cancellationToken) =>
        Task.FromResult<IReadOnlyList<ConversationSummary>>([]);

    public Task<ConversationDetail?> ConversationAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken) =>
        Task.FromResult(Detail);

    public Task<Guid?> RecordAsync(ClaimsPrincipal user, Guid? conversationId, string question, string answer, CancellationToken cancellationToken)
    {
        Recorded.Add((conversationId, question, answer));
        return Task.FromResult(RecordedAs);
    }
}