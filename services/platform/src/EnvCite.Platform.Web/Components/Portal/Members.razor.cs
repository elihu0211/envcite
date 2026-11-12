using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class Members(IMemberSelfService team, IMailSender mail)
{
    [SupplyParameterFromForm(FormName = "portal-member-add")] private AddForm Add { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "portal-member-action")] private ActionForm Input { get; set; } = default!;

    private IReadOnlyList<TeamMember>? _team;
    private IReadOnlyList<string> _grantable = [];
    private IssuedPassword? _issued;
    private string? _issuedPassword;
    private string _issuedTitle = string.Empty;
    private string? _message;
    protected override async Task OnInitializedAsync()
    {
        Add ??= new();
        Input ??= new();
        _grantable = team.GrantableRoles(HttpContext.User);
        Add.Role = string.IsNullOrEmpty(Add.Role) ? (_grantable.Count > 0 ? _grantable[^1] : string.Empty) : Add.Role;
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        var listed = await team.ListAsync(HttpContext.User, HttpContext.RequestAborted);
        _team = listed.IsOk ? listed.Value : null;
    }

    private async Task AddAsync()
    {
        // 有寄信就邀請（對方自己設密碼）；沒有就發暫時密碼，由你親手交付。
        if (mail.Enabled)
        {
            var invited = await team.InviteAsync(HttpContext.User, Add.Email, Add.DisplayName, Add.Role, HttpContext.RequestAborted);
            if (invited.IsOk)
            {
                if (invited.Value!.Mail == MailDelivery.Sent)
                {
                    _message = L["已寄出邀請信給 {0}。", invited.Value.Member.Email];
                }
                else
                {
                    Error = L["邀請信寄送失敗，{0} 的帳號已建立。請按「重寄邀請」，或用「重設密碼」取得暫時密碼。", invited.Value.Member.Email];
                }

                Add = new() { Role = Add.Role };
                await LoadAsync();
                return;
            }

            Error = Describe(invited);
            return;
        }

        var created = await team.CreateAsync(HttpContext.User, Add.Email, Add.DisplayName, Add.Role, HttpContext.RequestAborted);
        if (created.IsOk)
        {
            Reveal(L["新成員已建立"], created.Value!);
            Add = new() { Role = Add.Role };
            await LoadAsync();
            return;
        }

        Error = Describe(created);
    }

    private async Task ActAsync()
    {
        var action = Input.Action ?? string.Empty;
        var split = action.IndexOf(':', StringComparison.Ordinal);
        var (verb, id) = split < 0 ? (action, string.Empty) : (action[..split], action[(split + 1)..]);

        switch (verb)
        {
            case "disable":
            case "enable":
                var toggled = await team.SetDisabledAsync(HttpContext.User, id, verb == "disable", HttpContext.RequestAborted);
                Report(toggled, verb == "disable" ? L["已停用，對方所有登入立刻失效。"] : L["已重新啟用。"]);
                break;
            case "role":
                var role = HttpContext.Request.HasFormContentType ? HttpContext.Request.Form[$"role-{id}"].ToString() : string.Empty;
                var changed = await team.ChangeRoleAsync(HttpContext.User, id, role, HttpContext.RequestAborted);
                Report(changed, L["已變更角色。對方的舊登入已失效，下次登入才會套用新角色。"]);
                break;
            case "resend":
                var resent = await team.ResendInvitationAsync(HttpContext.User, id, HttpContext.RequestAborted);
                if (resent.IsOk && resent.Value!.Mail == MailDelivery.Sent)
                {
                    _message = L["已寄出信件給 {0}。", resent.Value.Member.Email];
                }
                else if (resent.IsOk)
                {
                    Error = L["寄信失敗，請稍後再試。"];
                }
                else
                {
                    Error = Describe(resent);
                }

                break;
            case "reset":
                var reset = await team.ResetPasswordAsync(HttpContext.User, id, HttpContext.RequestAborted);
                if (reset.IsOk)
                {
                    Reveal(L["新的一次性密碼"], reset.Value!);
                }
                else
                {
                    Error = Describe(reset);
                }

                break;
            default:
                Error = L["不認得這個動作。"];
                break;
        }

        await LoadAsync();
    }

    private void Reveal(string title, IssuedPassword issued)
    {
        (_issued, _issuedTitle, _issuedPassword) = (issued, title, issued.Password);
    }

    private void Report<T>(AdminOutcome<T> outcome, string success)
    {
        if (outcome.IsOk)
        {
            _message = success;
        }
        else
        {
            Error = Describe(outcome);
        }
    }

    private string Describe<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.NotFound => L["找不到這位成員。"],
        _ => outcome.Localized() ?? L["操作失敗。"],
    };

    private string State(MemberInfo m) =>
        m.Disabled ? L["已停用"] : m.LockedOut ? L["已鎖定"] : m.Invited ? L["待啟用"] : m.MustChangePassword ? L["待改密碼"] : L["正常"];

    private bool CanResend(MemberInfo m) => mail.Enabled && !m.Disabled && (m.Invited || !m.EmailConfirmed);

    private static string StateTone(MemberInfo m) =>
        m.Disabled ? "neutral" : m.LockedOut ? "error" : m.Invited || m.MustChangePassword ? "warning" : "success";

    public sealed class AddForm
    {
        public string Email { get; set; } = string.Empty;

        public string DisplayName { get; set; } = string.Empty;

        public string Role { get; set; } = string.Empty;
    }

    public sealed class ActionForm
    {
        public string? Action { get; set; }
    }
}