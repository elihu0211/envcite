using System.Security.Claims;

using Microsoft.Extensions.AI;

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

    /// <summary>每次提問帶進來的前文，與 <see cref="Asked"/> 同序。</summary>
    public List<IReadOnlyList<ChatMessage>> Histories { get; } = [];

    public List<RecordedTurn> Recorded { get; } = [];

    public TaskCompletionSource? CapabilitiesGate { get; set; }

    public async Task<AskCapabilities> CapabilitiesAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (CapabilitiesGate is not null)
            await CapabilitiesGate.Task;

        return Capabilities;
    }

    public async Task<AskResult> AskAsync(
        ClaimsPrincipal user, string? question, string? asOf, IReadOnlyList<ChatMessage> history, IProgress<AnswerProgress>? progress, CancellationToken cancellationToken)
    {
        Asked.Add((question, asOf));
        Histories.Add(history);
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

    public Task<Guid?> RecordAsync(ClaimsPrincipal user, Guid? conversationId, ConversationTurn turn, CancellationToken cancellationToken)
    {
        Recorded.Add(new RecordedTurn(conversationId, turn));
        return Task.FromResult(RecordedAs);
    }

    /// <summary>記進歷史的一回合；<see cref="Question"/> 與 <see cref="Answer"/> 是常看的兩欄。</summary>
    public sealed record RecordedTurn(Guid? Conversation, ConversationTurn Turn)
    {
        public string Question => Turn.Question;

        public string Answer => Turn.Answer;
    }
}