using System.Globalization;
using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

namespace EnvCite.Platform.Modules.Conversations;

internal sealed class ConversationHistory(
    PlatformDbContext db,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IOptions<ConversationHistoryOptions> options,
    TimeProvider time) : IConversationHistory
{
    private bool Enabled => options.Value.Enabled;

    public async Task<Guid?> AppendTurnAsync(
        ClaimsPrincipal user, Guid? conversationId, ConversationTurn turn, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId || string.IsNullOrWhiteSpace(turn.Question))
            return null;

        var now = time.GetUtcNow();
        // 別人的、不存在的對話 Id 都當作另開一串：不回報它存不存在。
        var conversation = conversationId is { } id
            ? await db.Conversations.FirstOrDefaultAsync(c => c.Id == id && c.UserId == userId, cancellationToken)
            : null;
        if (conversation is null)
        {
            conversation = new Conversation { UserId = userId, Title = TitleOf(turn.Question), CreatedAt = now, UpdatedAt = now };
            db.Conversations.Add(conversation);
        }

        conversation.UpdatedAt = now;
        db.ConversationMessages.Add(new ConversationMessage
        {
            ConversationId = conversation.Id,
            Role = ConversationMessage.UserRole,
            Content = turn.Question.Trim(),
            CreatedAt = now,
            AsOf = turn.AsOf,
        });
        db.ConversationMessages.Add(new ConversationMessage
        {
            ConversationId = conversation.Id,
            Role = ConversationMessage.AssistantRole,
            Content = turn.Answer,
            // 資料庫只存到微秒：差一個 tick（100 奈秒）會存成同一個時間，讀回來的先後就不確定，接續對話時問答會對調。
            CreatedAt = now.AddMicroseconds(1),
            Outcome = turn.Outcome,
            Citations = [.. turn.Citations.Select(c => new StoredCitation { Code = c.Code, Label = c.Label, Uri = c.Uri })],
        });
        await db.SaveChangesAsync(cancellationToken);
        return conversation.Id;
    }

    public async Task<IReadOnlyList<ConversationSummary>> ListAsync(
        ClaimsPrincipal user, int limit, string? search, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
            return [];

        var mine = db.Conversations.Where(c => c.UserId == userId);
        if (search?.Trim() is { Length: > 0 } term)
        {
            // 使用者打的字不是萬用字元：先跳脫 \ % _，再交給資料庫做不分大小寫的比對。
            var escaped = term.Replace(@"\", @"\\", StringComparison.Ordinal).Replace("%", @"\%", StringComparison.Ordinal).Replace("_", @"\_", StringComparison.Ordinal);
            var pattern = $"%{escaped}%";
            mine = mine.Where(c => EF.Functions.ILike(c.Title, pattern, @"\"));
        }

        // 釘選的與最近的分開查：釘選的不佔「最近」的筆數，最近的也不會被一長串釘選擠掉。
        var pinned = await mine
            .Where(c => c.PinnedAt != null)
            .OrderByDescending(c => c.PinnedAt)
            .ThenByDescending(c => c.Id)
            .Take(IConversationHistory.MaxPinned)
            .Select(c => new ConversationSummary(c.Id, c.Title, c.UpdatedAt, true))
            .ToListAsync(cancellationToken);
        var recent = await mine
            .Where(c => c.PinnedAt == null)
            .OrderByDescending(c => c.UpdatedAt)
            .ThenByDescending(c => c.Id)
            .Take(Math.Clamp(limit, 1, 200))
            .Select(c => new ConversationSummary(c.Id, c.Title, c.UpdatedAt, false))
            .ToListAsync(cancellationToken);
        return [.. pinned, .. recent];
    }

    public async Task<ConversationDetail?> GetAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
            return null;

        var conversation = await db.Conversations
            .Where(c => c.Id == id && c.UserId == userId)
            .Select(c => new { c.Id, c.Title, c.UpdatedAt, Pinned = c.PinnedAt != null })
            .FirstOrDefaultAsync(cancellationToken);
        if (conversation is null)
            return null;

        // 實體整個讀回來再對映：引用是 jsonb 的擁有型別集合，這樣讀最穩。
        var stored = await db.ConversationMessages
            .AsNoTracking()
            .Where(m => m.ConversationId == id)
            .OrderBy(m => m.CreatedAt)
            .ToListAsync(cancellationToken);
        var messages = stored
            .Select(m => new ConversationMessageView(
                m.Role,
                m.Content,
                m.CreatedAt,
                [.. m.Citations.Select(c => new ConversationCitation(c.Code, c.Label, c.Uri))],
                m.AsOf,
                m.Outcome))
            .ToList();
        return new ConversationDetail(conversation.Id, conversation.Title, conversation.UpdatedAt, messages, conversation.Pinned);
    }

    public async Task<bool> RenameAsync(ClaimsPrincipal user, Guid id, string title, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId || NormalizeTitle(title) is not { Length: > 0 } normalized)
            return false;

        // 不經 SaveChanges 的單一 UPDATE：只改標題，UpdatedAt 不動（改名不是新的活動，不改排序也不延長保存）。
        return await db.Conversations
            .Where(c => c.Id == id && c.UserId == userId)
            .ExecuteUpdateAsync(set => set.SetProperty(c => c.Title, normalized), cancellationToken) > 0;
    }

    public async Task<bool> PinAsync(ClaimsPrincipal user, Guid id, bool pinned, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
            return false;

        DateTimeOffset? at = pinned ? time.GetUtcNow() : null;
        return await db.Conversations
            .Where(c => c.Id == id && c.UserId == userId)
            .ExecuteUpdateAsync(set => set.SetProperty(c => c.PinnedAt, at), cancellationToken) > 0;
    }

    public async Task<bool> DeleteAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
            return false;

        var conversation = await db.Conversations.FirstOrDefaultAsync(c => c.Id == id && c.UserId == userId, cancellationToken);
        if (conversation is null)
            return false;

        // 訊息由資料庫的 cascade 一起刪；稽核與刪除同一個交易，且只記對話 Id，不記任何內容。
        db.Conversations.Remove(conversation);
        db.StageAudit(
            user,
            new AuditEntry(AuditActions.ConversationDeleted, tenant.MultiTenantContext?.TenantInfo?.Id, "conversation", conversation.Id.ToString()),
            time);
        await db.SaveChangesAsync(cancellationToken);
        return true;
    }

    private static string? UserId(ClaimsPrincipal user) => user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value;

    /// <summary>標題取第一個問題的前 80 個字（以文字單位計，不切斷代理對）。</summary>
    private static string TitleOf(string question) => NormalizeTitle(question) ?? string.Empty;

    /// <summary>
    /// 標題只有一行：控制字元與連續空白合成一個空白、去頭尾，再截到 80 個文字單位（不切斷代理對）。空白就是 null。
    /// </summary>
    private static string? NormalizeTitle(string text)
    {
        var plain = string.Create(text.Length, text, static (span, source) =>
        {
            for (var i = 0; i < span.Length; i++)
                span[i] = char.IsControl(source[i]) || char.IsWhiteSpace(source[i]) ? ' ' : source[i];
        });
        var line = string.Join(' ', plain.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
        if (line.Length == 0)
            return null;

        var elements = StringInfo.GetTextElementEnumerator(line);
        var length = 0;
        var count = 0;
        while (count < Conversation.TitleLength && elements.MoveNext())
        {
            length = elements.ElementIndex + ((string)elements.Current).Length;
            count++;
        }

        return line[..length];
    }
}