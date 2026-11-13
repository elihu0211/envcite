using System.Security.Claims;

using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace EnvCite.Platform.Web.Components.Portal.Workspace;

/// <summary>
/// 入口的問答頁（互動式）：只管呈現與輸入。流程與狀態在 <see cref="ChatSession"/>，領域動作經 <see cref="IAskService"/>。
/// 沒有 <c>HttpContext</c>：身分取自基底的 <c>UserAsync</c>，租戶取自身分。
/// </summary>
public sealed partial class ChatPage
{
    // 範例是臺灣法規裡會出現的詞（資料庫是中文），所以英文介面也維持中文：翻成英文就搜不到。
    private static readonly string[] Examples = ["噪音管制標準", "固定污染源", "廢棄物清理", "水污染防治"];

    // 元件會在非同步工作還沒結束時被處置（授權元件重新評估、版面換掉），那時續行還會往下跑：
    // 權杖只取一次存起來，處置後存取 CancellationTokenSource.Token 會丟 ObjectDisposedException。
    private readonly CancellationTokenSource _cts = new();
    private readonly CancellationToken _ct;
    private bool _disposed;

    // 預先渲染的畫面先出現，circuit 連上之後才接手：之前的輸入與按鈕不會有人處理（表單會變成整頁重送、輸入的字消失），所以先停用。
    private bool _interactive;
    private ElementReference _input;
    private IJSObjectReference? _module;
    private string _query = string.Empty;
    private string _asOf = string.Empty;
    private string? _name;
    private bool _notFound;
    private bool _subscribed;
    private bool _scroll;
    private bool _refocus;

    public ChatPage() => _ct = _cts.Token;

    /// <summary>要開啟的對話；新對話的路由沒有這個參數（<see cref="Guid.Empty"/>）。</summary>
    [Parameter] public Guid Id { get; set; }

    protected override async Task OnParametersSetAsync()
    {
        var user = await UserAsync();
        if (_disposed)
            return;

        if (!_subscribed)
        {
            Session.Changed += OnChanged;
            _subscribed = true;
            _name = user.FindFirst(ClaimTypes.GivenName)?.Value ?? user.FindFirst(ClaimTypes.Email)?.Value;
            await Session.InitializeAsync(user, _ct);
        }

        if (_disposed)
            return; // 等待期間被處置了：這個實例不會再畫，新的實例自己會開啟對話

        if (Id == Guid.Empty)
        {
            // 回到新對話（例如點側欄的「查詢」）。問完一題後網址換成這串對話的 Id 是同一個頁面，不算。
            _notFound = false;
            if (Session.ConversationId is not null)
                Session.StartNew();
        }
        else if (Id != Session.ConversationId)
        {
            _notFound = !await Session.OpenAsync(user, Id, _ct);
        }
    }

    protected override async Task OnAfterRenderAsync(bool firstRender)
    {
        if (firstRender)
        {
            // OnAfterRender 只在互動式渲染才會跑（預先渲染沒有）：到這裡就是 circuit 已經接手。
            _interactive = true;
            StateHasChanged();
        }

        try
        {
            if (_scroll)
            {
                _scroll = false;
                _module ??= await JS.InvokeAsync<IJSObjectReference>("import", "./_content/EnvCite.Platform.Web/Components/Portal/Workspace/ChatPage.razor.js");
                await _module.InvokeVoidAsync("scrollToLastTurn");
            }

            if (_refocus && !Session.Busy)
            {
                _refocus = false;
                await _input.FocusAsync();
            }
        }
        catch (JSDisconnectedException)
        {
            // 連線已斷：沒有畫面可以捲了。
        }
    }

    /// <summary>等結果時說明進行到哪一步：找到幾條法源、正在寫、正在核對出處。搜尋沒有步驟，維持「正在查找法源」。</summary>
    private string PendingText(ChatTurn turn) => turn.Progress switch
    {
        { Stage: AnswerStage.Rewriting } => L["正在理解你的追問…"],
        { Stage: AnswerStage.Writing } progress => L["找到 {0} 條法源，正在撰寫回答…", progress.Sources],
        { Stage: AnswerStage.Verifying } => L["正在核對出處…"],
        _ => L["正在查找法源…"],
    };

    private Task SubmitAsync() => AskAsync(_query);

    private async Task AskAsync(string question)
    {
        _query = string.Empty;
        await Session.AskAsync(await UserAsync(), question, _asOf, _ct);
        _refocus = true;

        // 對話被記進歷史後，網址換成這串對話的 Id：重新整理、分享給自己都回到同一串。同一個頁面，不重新載入。
        if (Session.ConversationId is { } id && Id != id)
            Navigation.NavigateTo(PortalPaths.Conversation(Tenant, id), new NavigationOptions { ReplaceHistoryEntry = true });
    }

    private void NewConversation()
    {
        Session.StartNew();
        Navigation.NavigateTo(PortalPaths.Home(Tenant));
    }

    /// <summary>歷史裡存的答案是「正文，接著每個出處一行」；出處行用小字，正文是回答。</summary>
    private static bool IsCitationLine(string line) =>
        line.StartsWith('《') || (line.StartsWith("[L", StringComparison.Ordinal) && line.Contains("] ", StringComparison.Ordinal));

    private void OnChanged()
    {
        _scroll = true;
        _ = InvokeAsync(StateHasChanged);
    }

    public async ValueTask DisposeAsync()
    {
        _disposed = true;
        Session.Changed -= OnChanged;
        await _cts.CancelAsync();
        _cts.Dispose();
        if (_module is not null)
        {
            try
            {
                await _module.DisposeAsync();
            }
            catch (JSDisconnectedException)
            {
                // 連線已斷：模組隨連線消失。
            }
        }
    }
}