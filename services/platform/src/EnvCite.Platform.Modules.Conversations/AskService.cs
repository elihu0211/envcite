using System.Security.Claims;

namespace EnvCite.Platform.Modules.Conversations;

/// <summary>
/// 入口問答的應用服務（<see cref="IAskService"/>）。每個操作經 <see cref="TenantScopes"/> 開一個綁好呼叫者租戶的 scope，
/// 在裡面解析回答、搜尋與歷史：這些服務計量與存取都吃環境租戶，靜態頁有、互動式 circuit 沒有，所以不能直接注入頁面。
/// 租戶只取自入口成員的 claim；不是入口成員（沒有 <see cref="PlatformClaimTypes.PortalUserId"/>）一律當作沒有方案。
/// </summary>
internal sealed class AskService(
    TenantScopes tenants, ITenantDirectory directory, IAnswerAvailability availability, IConversationHistoryAvailability history) : IAskService
{
    private static readonly AskCapabilities None = new(HasPlan: false, AiAnswer: false);

    public async Task<AskCapabilities> CapabilitiesAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (MemberTenant(user) is not { } identifier)
            return None;

        var outcome = await tenants.InByIdentifierAsync<AskCapabilities>(
            identifier, async scope => AdminOutcome.Ok(await CapabilitiesOfAsync(scope, cancellationToken)));

        return outcome.IsOk ? outcome.Value! : None;
    }

    public async Task<AskResult> AskAsync(ClaimsPrincipal user, string? question, string? asOf, CancellationToken cancellationToken)
    {
        var noPlan = new AskResult(AskMode.Search, AskStatus.NoPlan);
        if (MemberTenant(user) is not { } identifier)
            return noPlan;

        var outcome = await tenants.InByIdentifierAsync<AskResult>(identifier, async scope =>
        {
            // 完全付費制：沒有方案不能用資料功能。頁面不顯示表單之外這裡再擋一次，因為表單可以被直接送出。
            var capabilities = await CapabilitiesOfAsync(scope, cancellationToken);
            if (!capabilities.HasPlan)
                return AdminOutcome.Ok(noPlan);

            if (capabilities.AiAnswer)
            {
                var answered = await scope.Get<IGroundedAnswerer>().AnswerAsync(question, asOf, cancellationToken);
                return AdminOutcome.Ok(new AskResult(AskMode.Answer, StatusOf(answered.Status), answered.IsOk ? answered.Value : null));
            }

            var searched = await scope.Get<ILawSearch>().SearchAsync(question, asOf, limit: null, cancellationToken);
            return AdminOutcome.Ok(new AskResult(AskMode.Search, StatusOf(searched.Status), Hits: searched.IsOk ? searched.Value : null));
        });

        return outcome.IsOk ? outcome.Value! : noPlan;
    }

    public async Task<IReadOnlyList<ConversationSummary>> RecentAsync(ClaimsPrincipal user, int limit, CancellationToken cancellationToken)
    {
        if (!history.Enabled || MemberTenant(user) is not { } identifier)
            return [];

        var outcome = await tenants.InByIdentifierAsync<IReadOnlyList<ConversationSummary>>(
            identifier,
            async scope => AdminOutcome.Ok(await scope.Get<IConversationHistory>().ListAsync(user, limit, cancellationToken)));

        return outcome.IsOk ? outcome.Value! : [];
    }

    public async Task<Guid?> RecordAsync(ClaimsPrincipal user, Guid? conversationId, string question, string answer, CancellationToken cancellationToken)
    {
        if (MemberTenant(user) is not { } identifier)
            return null;

        var outcome = await tenants.InByIdentifierAsync<Guid?>(
            identifier,
            async scope => AdminOutcome.Ok(await scope.Get<IConversationHistory>().AppendTurnAsync(user, conversationId, question, answer, cancellationToken)));

        return outcome.IsOk ? outcome.Value : null;
    }

    private async Task<AskCapabilities> CapabilitiesOfAsync(TenantScope scope, CancellationToken cancellationToken)
    {
        if (await directory.FindAsync(scope.Tenant.Id, cancellationToken) is not { Plan: not null } summary)
            return None;

        return new AskCapabilities(HasPlan: true, AiAnswer: summary.AiAnswer && availability.Available);
    }

    private static AskStatus StatusOf(AdminStatus status) => status switch
    {
        AdminStatus.Ok => AskStatus.Ok,
        AdminStatus.Invalid => AskStatus.Invalid,
        _ => AskStatus.Unavailable,
    };

    private static string? MemberTenant(ClaimsPrincipal user) =>
        user.Identity?.IsAuthenticated == true && user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { Length: > 0 } tenant ? tenant : null
            : null;
}