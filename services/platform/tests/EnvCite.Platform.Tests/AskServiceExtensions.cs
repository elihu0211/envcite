using System.Security.Claims;

namespace EnvCite.Platform.Tests;

internal static class AskServiceExtensions
{
    /// <summary>第一次提問（沒有前文）：多數測試不關心前文，只在測記憶的地方明確帶。</summary>
    public static Task<AskResult> AskAsync(
        this IAskService ask, ClaimsPrincipal user, string? question, string? asOf, IProgress<AnswerProgress>? progress, CancellationToken cancellationToken) =>
        ask.AskAsync(user, question, asOf, [], progress, cancellationToken);

    /// <summary>記一問一答（有出處的回答，引用寫在文字裡）：多數測試只關心「有一串對話存在」，不關心引用的結構。</summary>
    public static Task<Guid?> RecordAsync(
        this IAskService ask, ClaimsPrincipal user, Guid? conversationId, string question, string answer, CancellationToken cancellationToken) =>
        ask.RecordAsync(user, conversationId, new ConversationTurn(question, answer, ConversationOutcome.Answered, []), cancellationToken);
}