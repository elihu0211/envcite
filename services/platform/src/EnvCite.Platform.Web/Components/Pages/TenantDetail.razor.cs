using EnvCite.Platform.Web.Components.Dialogs;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Pages;

public partial class TenantDetail(
    ITenantDirectory directory, IApiKeyAdmin keys, IPlanAdmin plans, IMemberAdmin members, IUsageAdmin usage, IAuditQuery audit, IMailSender mail,
    IDirectoryAdmin directorySync, IDirectoryAvailability directoryFeature)
{
    [Parameter] public string TenantId { get; set; } = string.Empty;

    /// <summary>目前的分頁（網址的 <c>?tab=</c>）。分頁是連結，所以可以分享、可以上一頁；沒給或不認得就是第一個。</summary>
    [SupplyParameterFromQuery(Name = "tab")] public string? Tab { get; set; }

    private TenantSummary? _tenant;
    private IReadOnlyList<ApiKeyInfo> _keys = [];
    private IReadOnlyList<MemberInfo> _members = [];
    private IReadOnlyList<AuditRecord> _recent = [];
    private IReadOnlyList<PlanInfo> _plans = [];
    private IReadOnlyList<TenantUsageSummary> _usage = [];
    private DirectoryStatus? _directory;
    private int _usageDays;
    private string? _selectedPlan;
    private string? _loaded;

    protected override async Task OnParametersSetAsync()
    {
        if (_loaded == TenantId)
            return;

        _loaded = TenantId;
        ClearNotice();
        if (Prerendered is { } snapshot && snapshot.TenantId == TenantId)
        {
            Prerendered = null;
            Show(snapshot);
            return;
        }

        Prerendered = null;
        await LoadAsync();
        if (!RendererInfo.IsInteractive && Error is null)
            Prerendered = new(TenantId, _tenant, _keys, _members, _recent, _usage, _directory);
    }

    public sealed record TenantSnapshot(
        string TenantId, TenantSummary? Tenant, IReadOnlyList<ApiKeyInfo> Keys, IReadOnlyList<MemberInfo> Members,
        IReadOnlyList<AuditRecord> Recent, IReadOnlyList<TenantUsageSummary> Usage, DirectoryStatus? Directory);

    /// <summary>預先渲染查到的資料交給互動渲染，不再查一次。重新連線不還原：資料可能已經變了，要重查。</summary>
    [PersistentState(RestoreBehavior = RestoreBehavior.SkipLastSnapshot)]
    public TenantSnapshot? Prerendered { get; set; }

    private void Show(TenantSnapshot snapshot)
    {
        _plans = plans.Plans;
        (_tenant, _keys, _members, _recent, _usage, _directory) = (snapshot.Tenant, snapshot.Keys, snapshot.Members, snapshot.Recent, snapshot.Usage, snapshot.Directory);
        _selectedPlan = _tenant?.Plan;
        Loading = false;
    }

    private Task LoadAsync() => LoadingAsync(async () =>
    {
        _plans = plans.Plans;
        _tenant = (await directory.ListAsync(CancellationToken.None)).FirstOrDefault(t => t.Id == TenantId);
        if (_tenant is not null)
        {
            _selectedPlan = _tenant.Plan;
            await LoadDetailsAsync();
        }
    });

    // 動作之後只重載會變的部分，頁面不閃。
    private async Task LoadDetailsAsync()
    {
        var listedKeys = await keys.ListAsync(TenantId, CancellationToken.None);
        _keys = listedKeys.IsOk ? listedKeys.Value!.OrderByDescending(k => k.CreatedAt).ToList() : [];

        var listedMembers = await members.ListAsync(TenantId, CancellationToken.None);
        _members = listedMembers.IsOk ? listedMembers.Value! : [];

        var page = await audit.QueryAsync(new AuditFilter(Tenant: TenantId, Limit: 10), CancellationToken.None);
        _recent = page.Items;

        await LoadUsageAsync();

        // 功能面沒開就沒有這個區塊；租戶沒開連線時仍看得到（狀態、token），營運者才知道客戶設定到哪。
        var sync = directoryFeature.Available ? await directorySync.GetStatusAsync(TenantId, CancellationToken.None) : null;
        _directory = sync is { IsOk: true } ? sync.Value : null;
    }

    private async Task LoadUsageAsync()
    {
        DateTimeOffset? since = _usageDays switch
        {
            7 => DateTimeOffset.UtcNow.AddDays(-7),
            30 => DateTimeOffset.UtcNow.AddDays(-30),
            _ => null,
        };
        var usageResult = await usage.GetTenantUsageAsync(TenantId, since, CancellationToken.None);
        _usage = usageResult.IsOk ? usageResult.Value! : [];
    }

    private async Task SetUsageFilterAsync(int days)
    {
        if (_usageDays == days)
            return;

        _usageDays = days;
        await LoadUsageAsync();
    }

    private int TotalUsageCalls => _usage.Sum(u => u.Calls);

    private int TotalUsageUnits => _usage.Sum(u => u.Units);

    private string UsageRangeLabel => _usageDays == 0 ? L["全部期間"] : L["近 {0} 天", _usageDays];

    private async Task RefreshAsync()
    {
        _tenant = (await directory.ListAsync(CancellationToken.None)).FirstOrDefault(t => t.Id == TenantId);
        _selectedPlan = _tenant?.Plan;
        await LoadDetailsAsync();
    }

    /// <summary>客戶入口的登入網址。</summary>
    private string PortalLoginUrl => PublicUrl(PortalPaths.Login(_tenant?.Identifier ?? string.Empty));

    /// <summary>API Base URL（<c>/v2</c> 的根）。</summary>
    private string ApiBaseUrl => PublicUrl(ApiPaths.V2);

    /// <summary>
    /// 對外主機取自目前請求的來源（scheme 與主機），不另設設定：後台、入口與 API 同一個主機（Gateway 全部轉給 platform），
    /// 在 Gateway 之後由 forwarded headers 還原、互動電路則取自瀏覽器自己的位址，所以就是客戶實際連得到的那一個。
    /// 用 <see cref="Uri.AbsoluteUri"/>（保留百分比編碼），複製出去的網址才貼得進信件與瀏覽器。
    /// </summary>
    private string PublicUrl(string path) => new Uri(new Uri(Navigation.BaseUri), path).AbsoluteUri;

    /// <summary>身分來源要填的 SCIM Base URL。</summary>
    private string ScimUrl => PublicUrl(ScimPaths.Prefix);

    private async Task DisableDirectoryAsync()
    {
        var confirmed = await ConfirmAsync(
            L["關閉目錄同步"], L["關閉後，{0} 的身分來源推送立刻被拒絕。既有成員與 token 都不動，客戶可以自己重新開啟。確定？", _tenant!.Identifier],
            L["關閉"]);
        if (!confirmed)
            return;

        await RunAsync(async () =>
        {
            var off = await directorySync.DisableAsync(await ActorAsync(), TenantId, CancellationToken.None);
            if (!off.IsOk)
            {
                Fail(off.Localized() ?? L["關閉失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(L["已關閉目錄同步。"]);
        });
    }

    private async Task ReleaseManagedAsync(MemberInfo member)
    {
        var confirmed = await ConfirmAsync(
            L["解除目錄同步的管理"], L["解除後，{0} 的角色與停用回到人手上，身分來源要重新接管才會再動他。確定？", member.Email],
            L["解除管理"]);
        if (!confirmed)
            return;

        await RunAsync(async () =>
        {
            var released = await directorySync.ReleaseManagedAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!released.IsOk)
            {
                Fail(released.Localized() ?? L["操作失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(L["已解除 {0} 的目錄同步管理。", member.Email]);
        });
    }

    private async Task AddMemberAsync()
    {
        var dialog = await Dialogs.ShowAsync<CreateMemberDialog>(
            L["新增成員"], new DialogParameters<CreateMemberDialog> { { x => x.TenantId, TenantId } }, Narrow);
        var result = await dialog.Result;
        if (result is { Canceled: false, Data: IssuedPassword issued })
        {
            await RevealAsync(issued, L["新增成員"]);
            await RunAsync(async () =>
            {
                await RefreshAsync();
                Succeed(L["已新增 {0}。", issued.Member.Email]);
            });
        }
        else if (result is { Canceled: false, Data: MemberInvitation invitation })
        {
            await RunAsync(async () =>
            {
                await RefreshAsync();
                if (invitation.Mail == MailDelivery.Sent)
                    Succeed(L["已寄出邀請信給 {0}。", invitation.Member.Email]);
                else
                    Fail(L["邀請信寄送失敗，{0} 的帳號已建立。請按「重寄邀請」，或用「重設密碼」取得暫時密碼。", invitation.Member.Email]);
            });
        }
    }

    private bool CanResend(MemberInfo member) => mail.Enabled && !member.Disabled && (member.Invited || !member.EmailConfirmed);

    private async Task ResendInvitationAsync(MemberInfo member)
    {
        await RunAsync(async () =>
        {
            var sent = await members.ResendInvitationAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!sent.IsOk)
            {
                Fail(sent.Localized() ?? L["寄信失敗，請稍後再試。"]);
                return;
            }

            await RefreshAsync();
            if (sent.Value!.Mail == MailDelivery.Sent)
                Succeed(member.Invited ? L["已重寄邀請信給 {0}。", member.Email] : L["已寄出驗證信給 {0}。", member.Email]);
            else
                Fail(L["寄信失敗，請稍後再試。"]);
        });
    }

    private async Task ResetPasswordAsync(MemberInfo member)
    {
        var confirmed = await ConfirmAsync(
            L["重設密碼"], L["重設 {0} 的密碼後，這位成員在所有裝置上會立刻登出，要用新的一次性密碼登入並自己改掉。確定？", member.Email],
            L["重設密碼"]);
        if (!confirmed)
            return;

        await RunAsync(async () =>
        {
            var reset = await members.ResetPasswordAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Localized() ?? L["重設失敗。"]);
                return;
            }

            await RevealAsync(reset.Value!, L["重設密碼"]);
            await RefreshAsync();
            Succeed(L["已重設 {0} 的密碼。", member.Email]);
        });
    }

    private async Task SetDisabledAsync(MemberInfo member, bool disabled)
    {
        if (disabled)
        {
            var confirmed = await ConfirmAsync(
                L["停用成員"], L["停用 {0} 後，他在所有裝置上立刻登出、也不能再登入；帳號與稽核紀錄都保留，之後可以重新啟用。確定？", member.Email],
                L["停用"]);
            if (!confirmed)
                return;
        }

        await RunAsync(async () =>
        {
            var changed = await members.SetDisabledAsync(await ActorAsync(), TenantId, member.Id, disabled, CancellationToken.None);
            if (!changed.IsOk)
            {
                Fail(changed.Localized() ?? L["操作失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(disabled ? L["已停用 {0}。", member.Email] : L["已重新啟用 {0}。", member.Email]);
        });
    }

    private async Task ResetTwoFactorAsync(MemberInfo member)
    {
        var confirmed = await ConfirmAsync(
            L["重設兩步驟驗證"],
            L["這會關掉 {0} 的兩步驟驗證、丟掉他的驗證器金鑰與復原碼，並讓他在所有裝置上登出；之後只靠密碼就能登入，直到他重新設定。", member.Email]
            + L["請先用你們原本的管道確認對方是本人（不是別人冒名來要求重設）。確定？"],
            L["重設兩步驟"]);
        if (!confirmed)
            return;

        await RunAsync(async () =>
        {
            var reset = await members.ResetTwoFactorAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Localized() ?? L["重設失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(L["已重設 {0} 的兩步驟驗證。", member.Email]);
        });
    }

    private async Task RevealAsync(IssuedPassword issued, string title)
    {
        var reveal = await Dialogs.ShowAsync<PasswordRevealDialog>(title, new DialogParameters<PasswordRevealDialog>
        {
            { x => x.Member, issued.Member },
            { x => x.Password, issued.Password },
            { x => x.LoginPath, PortalLoginUrl },
        }, Narrow);
        await reveal.Result;
    }

    private string PlanLabel(PlanInfo plan) => L["{0}（每 {1} {2} 次）", plan.Name, FormatWindow(plan.Window), plan.PermitLimit];

    private string FormatWindow(TimeSpan window) =>
        window.TotalMinutes >= 1 && window.TotalMinutes % 1 == 0 ? L["{0} 分鐘", (int)window.TotalMinutes] : L["{0} 秒", (int)window.TotalSeconds];

    private Task SavePlanAsync() => ChangePlanAsync(_selectedPlan);

    private async Task RemovePlanAsync()
    {
        var confirmed = await ConfirmAsync(
            L["移除方案"], L["移除後，{0} 立刻不能呼叫資料端點（回 402）。確定？", _tenant!.Identifier],
            L["移除方案"]);
        if (confirmed)
            await ChangePlanAsync(null);
    }

    private async Task ToggleAiAnswerAsync()
    {
        var enable = !_tenant!.AiAnswer;
        if (enable)
        {
            // 開啟會讓這個租戶的問題離開我們的系統，所以要確認（關閉不用：它只會讓資料少流出去）。
            var confirmed = await ConfirmAsync(
                L["開啟 AI 回答"], L["{0} 成員提的問題與相關條文會送到外部的模型服務。請確認租戶已同意。確定開啟？", _tenant.Identifier],
                L["開啟"]);
            if (!confirmed)
                return;
        }

        await RunAsync(async () =>
        {
            var result = await plans.SetAiAnswerAsync(await ActorAsync(), TenantId, enable, CancellationToken.None);
            if (!result.IsOk)
            {
                Fail(result.Localized() ?? L["變更失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(enable ? L["已開啟 AI 回答。"] : L["已關閉 AI 回答。"]);
        });
    }

    private async Task ChangePlanAsync(string? plan)
    {
        await RunAsync(async () =>
        {
            var change = await plans.SetAsync(await ActorAsync(), TenantId, plan, CancellationToken.None);
            if (!change.IsOk)
            {
                Fail(change.Localized() ?? L["變更方案失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(change.Value!.Current is { } current ? L["已指派方案 {0}。", current] : L["已移除方案。"]);
        });
    }

    private async Task IssueKeyAsync()
    {
        var dialog = await Dialogs.ShowAsync<IssueApiKeyDialog>(
            L["簽發 API key"], new DialogParameters<IssueApiKeyDialog> { { x => x.TenantId, TenantId } }, Narrow);
        var result = await dialog.Result;
        if (result is { Canceled: false })
        {
            await RunAsync(async () =>
            {
                await RefreshAsync();
                Succeed(L["已簽發新的 API key。"]);
            });
        }
    }

    private async Task RevokeKeyAsync(ApiKeyInfo key)
    {
        var confirmed = await ConfirmAsync(
            L["撤銷 API key"], L["撤銷「{0}」（{1}…）後，持有它的客戶立刻無法呼叫 API，且無法復原。確定？", key.Name, key.Prefix],
            L["撤銷"]);
        if (!confirmed)
            return;

        await RunAsync(async () =>
        {
            var revoked = await keys.RevokeAsync(await ActorAsync(), TenantId, key.Id, CancellationToken.None);
            if (!revoked.IsOk)
            {
                Fail(revoked.Localized() ?? L["撤銷失敗。"]);
                return;
            }

            await RefreshAsync();
            Succeed(L["已撤銷「{0}」。", key.Name]);
        });
    }
}