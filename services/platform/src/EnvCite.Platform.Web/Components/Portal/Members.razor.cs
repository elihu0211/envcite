using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class Members
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IMemberSelfService Team { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "portal-member-add")] private AddForm Add { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "portal-member-action")] private ActionForm Input { get; set; } = default!;

    private IReadOnlyList<TeamMember>? _team;
    private IReadOnlyList<string> _grantable = [];
    private IssuedPassword? _issued;
    private string? _issuedPassword;
    private string _issuedTitle = string.Empty;
    private string? _message;
    private string? _error;

    protected override async Task OnInitializedAsync()
    {
        Add ??= new();
        Input ??= new();
        _grantable = Team.GrantableRoles(HttpContext.User);
        Add.Role = string.IsNullOrEmpty(Add.Role) ? (_grantable.Count > 0 ? _grantable[^1] : string.Empty) : Add.Role;
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        var team = await Team.ListAsync(HttpContext.User, HttpContext.RequestAborted);
        _team = team.IsOk ? team.Value : null;
    }

    private async Task AddAsync()
    {
        var created = await Team.CreateAsync(HttpContext.User, Add.Email, Add.DisplayName, Add.Role, HttpContext.RequestAborted);
        if (created.IsOk)
        {
            Reveal("新成員已建立", created.Value!);
            Add = new() { Role = Add.Role };
            await LoadAsync();
            return;
        }

        _error = Describe(created);
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
                var toggled = await Team.SetDisabledAsync(HttpContext.User, id, verb == "disable", HttpContext.RequestAborted);
                Report(toggled, verb == "disable" ? "已停用，對方所有登入立刻失效。" : "已重新啟用。");
                break;
            case "role":
                var role = HttpContext.Request.HasFormContentType ? HttpContext.Request.Form[$"role-{id}"].ToString() : string.Empty;
                var changed = await Team.ChangeRoleAsync(HttpContext.User, id, role, HttpContext.RequestAborted);
                Report(changed, "已變更角色。對方的舊登入已失效，下次登入才會套用新角色。");
                break;
            case "reset":
                var reset = await Team.ResetPasswordAsync(HttpContext.User, id, HttpContext.RequestAborted);
                if (reset.IsOk)
                {
                    Reveal("新的一次性密碼", reset.Value!);
                }
                else
                {
                    _error = Describe(reset);
                }

                break;
            default:
                _error = "不認得這個動作。";
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
            _error = Describe(outcome);
        }
    }

    private static string Describe<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.NotFound => "找不到這位成員。",
        _ => outcome.Message ?? "操作失敗。",
    };

    private static string State(MemberInfo m) =>
        m.Disabled ? "已停用" : m.LockedOut ? "已鎖定" : m.MustChangePassword ? "待改密碼" : "正常";

    private static string StateTone(MemberInfo m) =>
        m.Disabled ? "neutral" : m.LockedOut ? "error" : m.MustChangePassword ? "warning" : "success";

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