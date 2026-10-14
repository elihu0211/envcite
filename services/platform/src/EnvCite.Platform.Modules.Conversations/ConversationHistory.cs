using System.Globalization;
using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Conversations;

internal sealed class ConversationHistory(
    PlatformDbContext db,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IOptions<ConversationHistoryOptions> options,
    TimeProvider time) : IConversationHistory
{
    public bool Enabled => options.Value.Enabled;

    public int RetentionDays => options.Value.RetentionDays;

    public async Task<Guid?> AppendTurnAsync(
        ClaimsPrincipal user, Guid? conversationId, string question, string answer, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId || string.IsNullOrWhiteSpace(question))
        {
            return null;
        }

        var now = time.GetUtcNow();
        // 別人的、不存在的對話 Id 都當作另開一串：不回報它存不存在。
        var conversation = conversationId is { } id
            ? await db.Conversations.FirstOrDefaultAsync(c => c.Id == id && c.UserId == userId, cancellationToken)
            : null;
        if (conversation is null)
        {
            conversation = new Conversation { UserId = userId, Title = TitleOf(question), CreatedAt = now, UpdatedAt = now };
            db.Conversations.Add(conversation);
        }

        conversation.UpdatedAt = now;
        db.ConversationMessages.Add(new ConversationMessage
        {
            ConversationId = conversation.Id,
            Role = ConversationMessage.UserRole,
            Content = question.Trim(),
            CreatedAt = now,
        });
        db.ConversationMessages.Add(new ConversationMessage
        {
            ConversationId = conversation.Id,
            Role = ConversationMessage.AssistantRole,
            Content = answer,
            CreatedAt = now.AddTicks(1),
        });
        await db.SaveChangesAsync(cancellationToken);
        return conversation.Id;
    }

    public async Task<IReadOnlyList<ConversationSummary>> ListAsync(ClaimsPrincipal user, int limit, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
        {
            return [];
        }

        return await db.Conversations
            .Where(c => c.UserId == userId)
            .OrderByDescending(c => c.UpdatedAt)
            .ThenByDescending(c => c.Id)
            .Take(Math.Clamp(limit, 1, 200))
            .Select(c => new ConversationSummary(c.Id, c.Title, c.UpdatedAt))
            .ToListAsync(cancellationToken);
    }

    public async Task<ConversationDetail?> GetAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
        {
            return null;
        }

        var conversation = await db.Conversations
            .Where(c => c.Id == id && c.UserId == userId)
            .Select(c => new { c.Id, c.Title, c.UpdatedAt })
            .FirstOrDefaultAsync(cancellationToken);
        if (conversation is null)
        {
            return null;
        }

        var messages = await db.ConversationMessages
            .Where(m => m.ConversationId == id)
            .OrderBy(m => m.CreatedAt)
            .Select(m => new ConversationMessageView(m.Role, m.Content, m.CreatedAt))
            .ToListAsync(cancellationToken);
        return new ConversationDetail(conversation.Id, conversation.Title, conversation.UpdatedAt, messages);
    }

    public async Task<bool> DeleteAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken)
    {
        if (!Enabled || UserId(user) is not { } userId)
        {
            return false;
        }

        var conversation = await db.Conversations.FirstOrDefaultAsync(c => c.Id == id && c.UserId == userId, cancellationToken);
        if (conversation is null)
        {
            return false;
        }

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
    private static string TitleOf(string question)
    {
        var text = question.Trim();
        var elements = StringInfo.GetTextElementEnumerator(text);
        var length = 0;
        var count = 0;
        while (count < Conversation.TitleLength && elements.MoveNext())
        {
            length = elements.ElementIndex + ((string)elements.Current).Length;
            count++;
        }

        return text[..length];
    }
}