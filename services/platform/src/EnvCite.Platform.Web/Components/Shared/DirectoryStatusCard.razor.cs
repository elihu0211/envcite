using System.Globalization;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class DirectoryStatusCard(IDirectoryAvailability availability, TimeProvider time)
{
    [Parameter, EditorRequired]
    public DirectoryStatus Status { get; set; } = default!;

    /// <summary>身分來源要填的 SCIM Base URL（含 <c>/scim/v2</c>）。</summary>
    [Parameter, EditorRequired]
    public string BaseUrl { get; set; } = string.Empty;

    /// <summary>同一頁有不只一張時，每張的 id 不能重複。</summary>
    [Parameter]
    public string Id { get; set; } = "directory";

    /// <summary>入口（靜態渲染）用純表格外觀，後台用 MudBlazor 的表格。</summary>
    [Parameter]
    public bool Plain { get; set; }

    /// <summary>標題列右側的動作（例如營運者的緊急關閉）。</summary>
    [Parameter]
    public RenderFragment? Actions { get; set; }

    /// <summary>每把 token 那一列的動作（例如撤銷）；沒給就沒有動作欄。</summary>
    [Parameter]
    public RenderFragment<DirectoryTokenInfo>? TokenActions { get; set; }

    /// <summary>每個群組那一列的動作（例如選對映的角色）；沒給就沒有動作欄，營運者只看。</summary>
    [Parameter]
    public RenderFragment<DirectoryGroupInfo>? GroupActions { get; set; }

    /// <summary>卡片底部：設定與簽發的表單等。</summary>
    [Parameter]
    public RenderFragment? ChildContent { get; set; }

    private DateTimeOffset Now => time.GetUtcNow();

    private (string Tone, string Text) Connection =>
        !Status.Settings.Enabled ? ("neutral", L["已關閉"])
        : Status.LastRequestAt is not { } last ? ("warning", L["等待身分來源連線"])
        : Now - last > availability.StaleAfter ? ("warning", L["久未收到請求"])
        : ("success", L["運作中"]);

    private string ConnectionTone => Connection.Tone;

    private string ConnectionText => Connection.Text;

    private IReadOnlyList<StatItem> Stats =>
    [
        new(L["受管成員"], Status.ManagedMembers.ToString("N0", CultureInfo.CurrentCulture), L["由身分來源管理角色與停用"]),
        new(L["最後收到請求"], Status.LastRequestAt is { } last ? TaipeiTime.Clock(last) : "—",
            Status.LastRequestAt is { } at ? L["台北 {0}", TaipeiTime.Date(at)].Value : L["尚未連線"].Value),
        new(L["同步次數"], (Status.Created + Status.Updated + Status.Deactivated).ToString("N0", CultureInfo.CurrentCulture),
            L["建立 {0}・更新 {1}・停用 {2}", Status.Created, Status.Updated, Status.Deactivated]),
        new(L["被拒絕"], Status.Rejected.ToString("N0", CultureInfo.CurrentCulture), L["規則不允許的變更"]),
    ];

    private (string Tone, string Text) TokenState(DirectoryTokenInfo token) =>
        token.RevokedAt is not null ? ("neutral", L["已撤銷"])
        : token.ExpiresAt is { } expires && expires <= Now ? ("warning", L["已過期"])
        : ("success", L["有效"]);
}