using Microsoft.Extensions.AI;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 假的模型用戶端：回答由測試決定，記下收到的每一次呼叫與選項。測的是我們的流程與檢查，不是某個模型的行為。
/// </summary>
internal sealed class FakeChat(Func<IReadOnlyList<ChatMessage>, string> reply, UsageDetails? usage = null) : IChatClient
{
    public List<IReadOnlyList<ChatMessage>> Calls { get; } = [];

    public ChatOptions? LastOptions { get; private set; }

    public Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
    {
        var list = messages.ToList();
        Calls.Add(list);
        LastOptions = options;
        return Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, reply(list))) { Usage = usage });
    }

    public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
        throw new NotSupportedException();

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose()
    {
    }
}