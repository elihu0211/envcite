using System.Security.Claims;

using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Components.Portal.Workspace;
using EnvCite.Platform.Web.Presentation.Output;
using EnvCite.Platform.Web.Resources;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Rendering;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.JSInterop;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 工作區頁面的生命週期。授權元件重新評估、版面換掉時，頁面會在非同步工作還沒結束時被處置，那時續行還會往下跑：
/// 續行不能因為處置而丟例外（例如存取已處置的 <see cref="CancellationTokenSource"/>），否則整個請求變成 500。
/// 不經瀏覽器與資料庫：用 <see cref="HtmlRenderer"/> 渲染頁面，領域動作換成假的 <see cref="IAskService"/>。
/// </summary>
public sealed class ChatPageLifecycleTests
{
    private static readonly ClaimsPrincipal User = new(new ClaimsIdentity(
        [new Claim(PlatformClaimTypes.PortalUserId, "u1"), new Claim(PlatformClaimTypes.TenantIdentifier, "acme")], "portal"));

    /// <summary>頁面的外框：可以在渲染途中把頁面拿掉（等同授權元件回到「授權中」）。</summary>
    private sealed class Host : ComponentBase
    {
        private bool _show = true;

        [Parameter] public Guid ConversationId { get; set; }

        /// <summary>渲染器自己建立這個元件；測試經它拿到實例。</summary>
        [Parameter] public Action<Host>? Created { get; set; }

        protected override void OnInitialized() => Created?.Invoke(this);

        public void Hide()
        {
            _show = false;
            StateHasChanged();
        }

        protected override void BuildRenderTree(RenderTreeBuilder builder)
        {
            if (!_show)
                return;

            builder.OpenComponent<CascadingValue<Task<AuthenticationState>>>(0);
            builder.AddComponentParameter(1, nameof(CascadingValue<>.Value), Task.FromResult(new AuthenticationState(User)));
            builder.AddComponentParameter(2, nameof(CascadingValue<>.ChildContent), (RenderFragment)(page =>
            {
                page.OpenComponent<ChatPage>(0);
                page.AddComponentParameter(1, nameof(ChatPage.Tenant), "acme");
                page.AddComponentParameter(2, nameof(ChatPage.Id), ConversationId);
                page.CloseComponent();
            }));
            builder.CloseComponent();
        }
    }

    private sealed class NoJs : IJSRuntime
    {
        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, object?[]? args) => throw new JSDisconnectedException("no browser");

        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, CancellationToken cancellationToken, object?[]? args) =>
            throw new JSDisconnectedException("no browser");
    }

    private sealed class HistoryOff : IConversationHistoryAvailability
    {
        public bool Enabled => false;

        public int RetentionDays => 0;
    }

    private sealed class FakeCitationArticleService : ICitationArticleService
    {
        public Task<AdminOutcome<PortalArticleDetail>> GetByIriAsync(
            ClaimsPrincipal user, string? iri, CancellationToken cancellationToken) =>
            Task.FromResult(AdminOutcome.NotFound<PortalArticleDetail>("NOT_FOUND"));
    }

    private sealed class TestNavigation : NavigationManager
    {
        public TestNavigation() => Initialize("https://localhost/", "https://localhost/portal/acme");
    }

    [Fact]
    public async Task APageDisposedWhileItsFirstLoadIsStillRunningDoesNotFailTheRender()
    {
        var gate = new TaskCompletionSource();
        var ask = new FakeAsk { CapabilitiesGate = gate };
        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IAskService>(ask)
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddOutputLayoutEngine()
            .AddSingleton<IConversationHistoryAvailability>(new HistoryOff())
            .AddSingleton<ICitationArticleService>(new FakeCitationArticleService())
            .AddSingleton<IJSRuntime>(new NoJs())
            .AddSingleton<BrandText>(new BrandText(Options.Create(new BrandOptions()), localizer))
            .AddSingleton<NavigationManager>(new TestNavigation())
            .AddSingleton<WorkspaceState>()
            .AddSingleton<ChatSession>()
            .AddSingleton<ILogger<ChatSession>>(NullLogger<ChatSession>.Instance)
            .BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);

        Host? host = null;
        var started = await renderer.Dispatcher.InvokeAsync(() => renderer.BeginRenderingComponent<Host>(ParameterView.FromDictionary(
            new Dictionary<string, object?> { [nameof(Host.ConversationId)] = Guid.NewGuid(), [nameof(Host.Created)] = (Action<Host>)(h => host = h) })));

        Assert.False(started.QuiescenceTask.IsCompleted); // 卡在 InitializeAsync 的等待上
        await renderer.Dispatcher.InvokeAsync(host!.Hide); // 頁面被處置
        gate.SetResult(); // 續行此時才跑：頁面已經不在了

        await started.QuiescenceTask.WaitAsync(TimeSpan.FromSeconds(10), TestContext.Current.CancellationToken);
    }
}