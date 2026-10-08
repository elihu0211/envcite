using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Components.Dialogs;
using EnvLaw.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;

using MudBlazor;

namespace EnvLaw.Platform.Web.Components.Pages;

public partial class TenantDetail
{
    private static readonly DialogOptions Narrow = new() { MaxWidth = MaxWidth.Small, FullWidth = true, CloseButton = true };

    [Parameter] public string TenantId { get; set; } = string.Empty;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private IApiKeyAdmin Keys { get; set; } = default!;

    [Inject] private IPlanAdmin Plans { get; set; } = default!;

    [Inject] private IMemberAdmin Members { get; set; } = default!;

    [Inject] private NavigationManager Navigation { get; set; } = default!;

    [Inject] private IAuditQuery Audit { get; set; } = default!;

    [Inject] private IDialogService Dialogs { get; set; } = default!;

    [Inject] private AuthenticationStateProvider Auth { get; set; } = default!;

    private TenantSummary? _tenant;
    private IReadOnlyList<ApiKeyInfo> _keys = [];
    private IReadOnlyList<MemberInfo> _members = [];
    private IReadOnlyList<AuditRecord> _recent = [];
    private IReadOnlyList<PlanInfo> _plans = [];
    private string? _selectedPlan;
    private bool _loading = true;
    private bool _busy;
    private string? _error;
    private string? _notice;
    private Severity _noticeSeverity = Severity.Success;
    private string? _loaded;

    protected override async Task OnParametersSetAsync()
    {
        if (_loaded == TenantId)
        {
            return;
        }

        _loaded = TenantId;
        _notice = null;
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        _loading = true;
        _error = null;
        try
        {
            _plans = Plans.Plans;
            _tenant = (await Directory.ListAsync(CancellationToken.None)).FirstOrDefault(t => t.Id == TenantId);
            if (_tenant is not null)
            {
                _selectedPlan = _tenant.Plan;
                await LoadDetailsAsync();
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            _error = exception.GetType().Name;
        }
        finally
        {
            _loading = false;
        }
    }

    // 動作之後只重載會變的部分，頁面不閃。
    private async Task LoadDetailsAsync()
    {
        var keys = await Keys.ListAsync(TenantId, CancellationToken.None);
        _keys = keys.IsOk ? keys.Value!.OrderByDescending(k => k.CreatedAt).ToList() : [];

        var members = await Members.ListAsync(TenantId, CancellationToken.None);
        _members = members.IsOk ? members.Value! : [];

        var page = await Audit.QueryAsync(new AuditFilter(Tenant: TenantId, Limit: 10), CancellationToken.None);
        _recent = page.Items;
    }

    private async Task RefreshAsync()
    {
        _tenant = (await Directory.ListAsync(CancellationToken.None)).FirstOrDefault(t => t.Id == TenantId);
        _selectedPlan = _tenant?.Plan;
        await LoadDetailsAsync();
    }

    /// <summary>給客戶的完整登入網址（用目前後台的主機位址組出來）。</summary>
    private string PortalLoginUrl => new Uri(new Uri(Navigation.BaseUri), PortalPaths.Login(_tenant?.Identifier ?? string.Empty)).ToString();

    private async Task AddMemberAsync()
    {
        var dialog = await Dialogs.ShowAsync<CreateMemberDialog>(
            "新增成員", new DialogParameters<CreateMemberDialog> { { x => x.TenantId, TenantId } }, Narrow);
        var result = await dialog.Result;
        if (result is { Canceled: false, Data: IssuedPassword issued })
        {
            await RevealAsync(issued, "新增成員");
            await RunAsync(async () =>
            {
                await RefreshAsync();
                Succeed($"已新增 {issued.Member.Email}。");
            });
        }
    }

    private async Task ResetPasswordAsync(MemberInfo member)
    {
        var confirmed = await Dialogs.ShowMessageBoxAsync(
            "重設密碼", $"重設 {member.Email} 的密碼後，這位成員在所有裝置上會立刻登出，要用新的一次性密碼登入並自己改掉。確定？",
            yesText: "重設密碼", cancelText: "取消");
        if (confirmed != true)
        {
            return;
        }

        await RunAsync(async () =>
        {
            var reset = await Members.ResetPasswordAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Message ?? "重設失敗。");
                return;
            }

            await RevealAsync(reset.Value!, "重設密碼");
            await RefreshAsync();
            Succeed($"已重設 {member.Email} 的密碼。");
        });
    }

    private async Task ResetTwoFactorAsync(MemberInfo member)
    {
        var confirmed = await Dialogs.ShowMessageBoxAsync(
            "重設兩步驟驗證",
            $"這會關掉 {member.Email} 的兩步驟驗證、丟掉他的驗證器金鑰與復原碼，並讓他在所有裝置上登出；之後只靠密碼就能登入，直到他重新設定。"
            + "請先用你們原本的管道確認對方是本人（不是別人冒名來要求重設）。確定？",
            yesText: "重設兩步驟", cancelText: "取消");
        if (confirmed != true)
        {
            return;
        }

        await RunAsync(async () =>
        {
            var reset = await Members.ResetTwoFactorAsync(await ActorAsync(), TenantId, member.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Message ?? "重設失敗。");
                return;
            }

            await RefreshAsync();
            Succeed($"已重設 {member.Email} 的兩步驟驗證。");
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

    private async Task<ClaimsPrincipal> ActorAsync() => (await Auth.GetAuthenticationStateAsync()).User;

    private static string PlanLabel(PlanInfo plan) => $"{plan.Name}（每 {FormatWindow(plan.Window)} {plan.PermitLimit} 次）";

    private static string FormatWindow(TimeSpan window) =>
        window.TotalMinutes >= 1 && window.TotalMinutes % 1 == 0 ? $"{(int)window.TotalMinutes} 分鐘" : $"{(int)window.TotalSeconds} 秒";

    private Task SavePlanAsync() => ChangePlanAsync(_selectedPlan);

    private async Task RemovePlanAsync()
    {
        var confirmed = await Dialogs.ShowMessageBoxAsync(
            "移除方案", $"移除後，{_tenant!.Identifier} 立刻不能呼叫資料端點（回 402）。確定？",
            yesText: "移除方案", cancelText: "取消");
        if (confirmed == true)
        {
            await ChangePlanAsync(null);
        }
    }

    private async Task ChangePlanAsync(string? plan)
    {
        await RunAsync(async () =>
        {
            var change = await Plans.SetAsync(await ActorAsync(), TenantId, plan, CancellationToken.None);
            if (!change.IsOk)
            {
                Fail(change.Message ?? "變更方案失敗。");
                return;
            }

            await RefreshAsync();
            Succeed(change.Value!.Current is { } current ? $"已指派方案 {current}。" : "已移除方案。");
        });
    }

    private async Task IssueKeyAsync()
    {
        var dialog = await Dialogs.ShowAsync<IssueApiKeyDialog>(
            "簽發 API key", new DialogParameters<IssueApiKeyDialog> { { x => x.TenantId, TenantId } }, Narrow);
        var result = await dialog.Result;
        if (result is { Canceled: false })
        {
            await RunAsync(async () =>
            {
                await RefreshAsync();
                Succeed("已簽發新的 API key。");
            });
        }
    }

    private async Task RevokeKeyAsync(ApiKeyInfo key)
    {
        var confirmed = await Dialogs.ShowMessageBoxAsync(
            "撤銷 API key", $"撤銷「{key.Name}」（{key.Prefix}…）後，持有它的客戶立刻無法呼叫 API，且無法復原。確定？",
            yesText: "撤銷", cancelText: "取消");
        if (confirmed != true)
        {
            return;
        }

        await RunAsync(async () =>
        {
            var revoked = await Keys.RevokeAsync(await ActorAsync(), TenantId, key.Id, CancellationToken.None);
            if (!revoked.IsOk)
            {
                Fail(revoked.Message ?? "撤銷失敗。");
                return;
            }

            await RefreshAsync();
            Succeed($"已撤銷「{key.Name}」。");
        });
    }

    // 動作期間停用按鈕，避免連點重送；失敗在頁面上說明，不吞掉也不讓電路掛掉。
    private async Task RunAsync(Func<Task> action)
    {
        _busy = true;
        try
        {
            await action();
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Fail("操作失敗，請稍後再試。(" + exception.GetType().Name + ")");
        }
        finally
        {
            _busy = false;
        }
    }

    private void Succeed(string message) => (_notice, _noticeSeverity) = (message, Severity.Success);

    private void Fail(string message) => (_notice, _noticeSeverity) = (message, Severity.Error);
}