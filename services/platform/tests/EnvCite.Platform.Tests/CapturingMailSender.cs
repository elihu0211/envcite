using System.Collections.Concurrent;

using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 收下寄出的信而不連外。<see cref="Enabled"/> 預設關（建立帳號走暫時密碼，與寄信無關的測試不受影響）；
/// 測邀請的測試用 <see cref="PlatformFactory.WithMail"/> 開啟，用完還原。<see cref="Result"/> 可模擬寄信失敗。
/// </summary>
public sealed class CapturingMailSender : IMailSender
{
    private readonly ConcurrentQueue<SentMail> _sent = new();

    public bool Enabled { get; set; }

    public MailDelivery Result { get; set; } = MailDelivery.Sent;

    public IReadOnlyCollection<SentMail> Sent => _sent;

    public Task<MailDelivery> SendAsync(string recipient, string subject, string body, CancellationToken cancellationToken)
    {
        _sent.Enqueue(new SentMail(recipient, subject, body));
        return Task.FromResult(Result);
    }

    public sealed record SentMail(string Recipient, string Subject, string Body);
}