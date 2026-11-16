namespace EnvCite.Platform.SharedKernel;

/// <summary>寄信的結果。寄不出去不讓動作失敗：呼叫端據此提示操作者重寄，或改用暫時密碼。</summary>
public enum MailDelivery
{
    /// <summary>沒有收件地址，沒有嘗試寄。</summary>
    NotRequested,

    /// <summary>部署者沒有啟用寄信（<c>Platform:Mail:Enabled</c>）。</summary>
    Disabled,

    /// <summary>中繼主機接受了信（只代表交給中繼，不代表對方收到）。</summary>
    Sent,

    /// <summary>連不上、逾時或被中繼拒絕。</summary>
    Failed,
}

/// <summary>寄一封 multipart/alternative 信（純文字加 HTML）。實作不丟例外（呼叫端自己的取消除外），失敗以 <see cref="MailDelivery.Failed"/> 回報。</summary>
public interface IMailSender
{
    /// <summary>是否啟用寄信。沒啟用時，建立帳號改走「暫時密碼」，由操作者親手交付。</summary>
    bool Enabled { get; }

    Task<MailDelivery> SendAsync(string recipient, string subject, MailContent content, CancellationToken cancellationToken);
}

/// <summary>一封信的內容：純文字版與 HTML 版，語意相同（同一條連結、同樣的期限與提示），以 multipart/alternative 寄出。</summary>
public sealed record MailContent(string Text, string Html);

/// <summary>寫好、可以寄的信：主旨加內容。收件者由呼叫端決定，傳輸層（<see cref="IMailSender"/>）只管寄。</summary>
public sealed record ComposedMail(string Subject, MailContent Content);

/// <summary>邀請（<see cref="Invitation"/>）或信箱驗證信要的資料：呼叫端只組這個，不碰文案與版面。</summary>
/// <param name="Link">單次、限時的啟用連結；信裡唯一的外部網址。</param>
/// <param name="Lifetime">連結的有效期限（一段時間，不是時間點）。</param>
/// <param name="TenantIdentifier">租戶識別碼；營運者沒有租戶，為 null。</param>
/// <param name="Invitation">邀請（帳號還沒有密碼）為 true，驗證信箱為 false。</param>
public sealed record ActivationMailModel(Uri Link, TimeSpan Lifetime, string? TenantIdentifier, bool Invitation);

/// <summary>
/// 把資料組成信：文案（<c>MailTexts</c> 資源檔，語言由 <c>Platform:Mail:Template:Culture</c> 決定）、系統名稱（<see cref="BrandOptions"/>）
/// 與版型（<c>Platform:Mail:Template</c>）合成主旨、純文字與 HTML。<b>不寄信、不連網路</b>，所以預覽命令與測試用的是同一個服務。
/// </summary>
public interface IMailComposer
{
    /// <summary>邀請信或信箱驗證信。信裡只有連結：不放密碼，也不放使用者的其他資料。</summary>
    Task<ComposedMail> ComposeActivationAsync(ActivationMailModel model, CancellationToken cancellationToken);

    /// <summary><c>mail test</c> 的測試信：沒有連結、沒有密碼，只確認寄信設定與中繼可用。</summary>
    Task<ComposedMail> ComposeTestAsync(CancellationToken cancellationToken);
}