using System.Security.Claims;

using EnvCite.Platform.Web.Components.Portal.Workspace;

using Microsoft.Extensions.Logging.Abstractions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 一串對話的狀態與流程：問一題、等結果、記進歷史、開啟舊的對話。純 C# 類別，所以不經瀏覽器、不經 circuit；
/// 領域動作換成假的 <see cref="IAskService"/>，測的是「這串對話怎麼對各種結果反應」。
/// </summary>
public sealed class ChatSessionTests
{
    private static readonly ClaimsPrincipal User = new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, "u1")], "portal"));

    private static CancellationToken Ct => TestContext.Current.CancellationToken;


    private static LawHit Hit(string title, string number) =>
        new($"https://example.test/{number}", new LawIdentifierDto("TW-CENTRAL", "pcode", "O0020001"), title, number, "內文", 1f);

    private static (ChatSession Session, FakeAsk Ask, WorkspaceState Workspace) Create(FakeAsk? ask = null)
    {
        ask ??= new FakeAsk();
        var workspace = new WorkspaceState();
        return (new ChatSession(ask, new KeyLocalizer(), workspace, NullLogger<ChatSession>.Instance), ask, workspace);
    }

    private static async Task<(ChatSession Session, FakeAsk Ask, WorkspaceState Workspace)> ReadyAsync(FakeAsk? ask = null)
    {
        var created = Create(ask);
        await created.Session.InitializeAsync(User, Ct);
        return created;
    }

    [Fact]
    public async Task AnAnsweredQuestionBecomesATurnWithItsSourcesAndIsRecordedWithItsCitations()
    {
        var (session, ask, workspace) = await ReadyAsync();
        var notified = 0;
        workspace.RecentChanged += () => notified++;

        await session.AskAsync(User, "  露天燃燒如何處罰  ", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal(("露天燃燒如何處罰", "現行", TurnState.Answered), (turn.Question, turn.AsOfLabel, turn.State));
        Assert.Equal("答 [L1]", turn.Answer!.Text);
        Assert.Equal([("露天燃燒如何處罰", (string?)null)], ask.Asked);
        var recorded = Assert.Single(ask.Recorded);
        Assert.Null(recorded.Conversation);
        Assert.Equal("露天燃燒如何處罰", recorded.Question);
        Assert.Equal("答 [L1]\n[L1] 空氣污染防制法 第 32 條（現行） https://example.test/law/32", recorded.Answer);
        Assert.Equal(ask.RecordedAs, session.ConversationId);
        Assert.Equal(1, notified); // 側欄的最近對話要重新讀
        Assert.False(session.Busy);
    }

    [Fact]
    public async Task ADateBecomesPartOfTheQuestionsLabelAndIsPassedOn()
    {
        var (session, ask, _) = await ReadyAsync();

        await session.AskAsync(User, "問題", " 2021-01-01 ", Ct);

        Assert.Equal("2021-01-01 有效", session.Turns[0].AsOfLabel);
        Assert.Equal([("問題", (string?)"2021-01-01")], ask.Asked);
        Assert.Contains("（2021-01-01 有效）", ask.Recorded[0].Answer, StringComparison.Ordinal);
    }

    // 拒答是結果，不是錯誤：說明為什麼沒有答案，記進歷史的也是這句話。
    [Theory]
    [InlineData("no_authority", "找不到足以回答這個問題的法條，所以沒有回答。試試換個說法，或換一天。")]
    [InlineData("out_of_scope", "這個問題超出目前涵蓋的知識範圍，所以沒有回答。")]
    [InlineData("local_law_uncovered", "這個問題問的是地方自治法規，而這裡只涵蓋中央法規，所以沒有回答。請向該地方政府查詢。")]
    [InlineData("missing_input", "問題不夠具體，請補充後再問。")]
    [InlineData("unverified_citation", "系統產生的回答無法對應到法條出處，已捨棄，沒有顯示。請換個說法再問一次。")]
    [InlineData("empty_answer", "這次沒有產生可以對應到法條出處的回答。請換個說法再問一次。")]
    public async Task ARefusalExplainsWhyThereIsNoAnswerAndIsWhatGetsRecorded(string reason, string label)
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk
        {
            Result = new AskResult(AskMode.Answer, AskStatus.Ok, Answer: new GroundedAnswer(null, [], reason, "detail")),
        });

        await session.AskAsync(User, "問題", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal(TurnState.Refused, turn.State);
        Assert.Equal(label, session.RefusalLabel(reason));
        Assert.Equal($"（拒答）{label}", Assert.Single(ask.Recorded).Answer);
    }

    [Fact]
    public async Task ASearchListsTheArticlesAndRecordsOnlyTheirCitations()
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk
        {
            Capabilities = new AskCapabilities(HasPlan: true, AiAnswer: false),
            Result = new AskResult(AskMode.Search, AskStatus.Ok, Hits: [Hit("噪音管制法", "9"), Hit("空氣污染防制法", "5-1")]),
        });

        await session.AskAsync(User, "噪音", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal((TurnState.Listed, 2), (turn.State, turn.Hits!.Count));
        Assert.Equal(
            "《噪音管制法》第 9 條（現行） https://example.test/9\n《空氣污染防制法》第 5-1 條（現行） https://example.test/5-1",
            Assert.Single(ask.Recorded).Answer); // 只有出處，沒有條文全文
    }

    [Fact]
    public async Task ASearchWithNoMatchIsStillAnAnswerAndIsRecorded()
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk { Result = new AskResult(AskMode.Search, AskStatus.Ok, Hits: []) });

        await session.AskAsync(User, "查無此詞", null, Ct);

        Assert.Equal(TurnState.Listed, session.Turns[0].State);
        Assert.Equal("沒有符合的條文。", Assert.Single(ask.Recorded).Answer);
    }

    // 輸入錯誤與服務不可用：回合留著、說明原因、不記進歷史；兩種模式說的話不同（問題 vs 關鍵字）。
    [Theory]
    [InlineData(AskMode.Answer, AskStatus.Invalid, "查詢條件不正確：請輸入問題；日期要是有效的日期。")]
    [InlineData(AskMode.Search, AskStatus.Invalid, "查詢條件不正確：請輸入關鍵字；日期要是有效的日期。")]
    [InlineData(AskMode.Answer, AskStatus.Unavailable, "回答服務暫時無法使用，請稍後再試。")]
    [InlineData(AskMode.Search, AskStatus.Unavailable, "搜尋服務暫時無法使用，請稍後再試。")]
    public async Task ABadInputOrAnOutageKeepsTheTurnExplainsItAndRecordsNothing(AskMode mode, AskStatus status, string message)
    {
        var (session, ask, workspace) = await ReadyAsync(new FakeAsk { Result = new AskResult(mode, status) });
        var notified = 0;
        workspace.RecentChanged += () => notified++;

        await session.AskAsync(User, "問題", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal((TurnState.Failed, message), (turn.State, turn.Error));
        Assert.Empty(ask.Recorded);
        Assert.Equal(0, notified);
        Assert.False(session.Busy);
    }

    // 沒有方案：沒有回合可畫，也不記。
    [Fact]
    public async Task WithoutAPlanTheTurnIsDroppedAndNothingIsRecorded()
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk { Result = new AskResult(AskMode.Search, AskStatus.NoPlan) });

        await session.AskAsync(User, "問題", null, Ct);

        Assert.Empty(session.Turns);
        Assert.Empty(ask.Recorded);
    }

    [Fact]
    public async Task AnUnexpectedFailureDoesNotBreakTheCircuitAndDoesNotLeakItsMessage()
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk { Throws = new InvalidOperationException("內部錯誤：客戶案情 ABC") });

        await session.AskAsync(User, "問題", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal(TurnState.Failed, turn.State);
        Assert.Equal("回答服務暫時無法使用，請稍後再試。", turn.Error);
        Assert.DoesNotContain("ABC", turn.Error, StringComparison.Ordinal);
        Assert.False(session.Busy);
        Assert.Empty(ask.Recorded);
    }

    [Fact]
    public async Task ABlankQuestionOrAMissingPlanIsIgnored()
    {
        var (session, ask, _) = await ReadyAsync();
        await session.AskAsync(User, "   ", null, Ct);
        await session.AskAsync(User, null, null, Ct);

        var (noPlan, noPlanAsk, _) = await ReadyAsync(new FakeAsk { Capabilities = new AskCapabilities(HasPlan: false, AiAnswer: false) });
        await noPlan.AskAsync(User, "問題", null, Ct);

        Assert.Empty(session.Turns);
        Assert.Empty(ask.Asked);
        Assert.Empty(noPlan.Turns);
        Assert.Empty(noPlanAsk.Asked);
    }

    // 一次只處理一題：等結果的時候再送一題（連按兩次、Enter 加按鈕）不會多問一次，也不會多記一次。
    [Fact]
    public async Task OnlyOneQuestionIsHandledAtATime()
    {
        var gate = new TaskCompletionSource();
        var (session, ask, _) = await ReadyAsync(new FakeAsk { Gate = gate });

        var first = session.AskAsync(User, "第一題", null, Ct);
        Assert.True(session.Busy);
        Assert.Equal(TurnState.Pending, session.Turns[0].State);
        await session.AskAsync(User, "第二題", null, Ct); // 被忽略
        gate.SetResult();
        await first;

        Assert.False(session.Busy);
        Assert.Equal(["第一題"], ask.Asked.Select(a => a.Question));
        Assert.Single(session.Turns);
        Assert.Single(ask.Recorded);
    }

    [Fact]
    public async Task AFollowUpContinuesTheSameConversationAndNewStartsAFreshOne()
    {
        var (session, ask, _) = await ReadyAsync();
        await session.AskAsync(User, "第一題", null, Ct);
        var conversation = session.ConversationId;

        await session.AskAsync(User, "第二題", null, Ct);

        Assert.Equal([null, conversation], ask.Recorded.Select(r => r.Conversation));
        Assert.Equal(2, session.Turns.Count);

        session.StartNew();

        Assert.Empty(session.Turns);
        Assert.Null(session.ConversationId);
    }

    // 歷史沒開啟：記不下來（回 null），對話沒有 Id，也不通知側欄。
    [Fact]
    public async Task WithoutHistoryThereIsNoConversationAndTheSidebarIsNotTold()
    {
        var (session, _, workspace) = await ReadyAsync(new FakeAsk { RecordedAs = null });
        var notified = 0;
        workspace.RecentChanged += () => notified++;

        await session.AskAsync(User, "問題", null, Ct);

        Assert.Single(session.Turns);
        Assert.Null(session.ConversationId);
        Assert.Equal(0, notified);
    }

    [Fact]
    public async Task OpeningAConversationRebuildsItsTurnsFromWhatWasStored()
    {
        var id = Guid.NewGuid();
        var at = DateTimeOffset.UtcNow;
        var (session, _, _) = await ReadyAsync(new FakeAsk
        {
            Detail = new ConversationDetail(id, "第一題", at,
            [
                new ConversationMessageView("user", "第一題", at),
                new ConversationMessageView("assistant", "答\n[L1] 出處 https://x", at),
                new ConversationMessageView("user", "第二題", at),
                new ConversationMessageView("assistant", "（拒答）原因", at),
            ]),
        });
        await session.AskAsync(User, "舊的，要被換掉", null, Ct);

        var found = await session.OpenAsync(User, id, Ct);

        Assert.True(found);
        Assert.Equal(id, session.ConversationId);
        Assert.Equal(["第一題", "第二題"], session.Turns.Select(t => t.Question));
        Assert.Equal(["答\n[L1] 出處 https://x", "（拒答）原因"], session.Turns.Select(t => t.StoredAnswer));
        Assert.All(session.Turns, t => Assert.Equal(TurnState.Answered, t.State));
        Assert.All(session.Turns, t => Assert.Null(t.AsOfLabel));
    }

    // 不是自己的、已刪除、已過期：同一個結果，而且舊的畫面不留下來。
    [Fact]
    public async Task AConversationThatCannotBeOpenedLeavesAnEmptyNewOne()
    {
        var (session, _, _) = await ReadyAsync(new FakeAsk { Detail = null });
        await session.AskAsync(User, "舊的", null, Ct);

        var found = await session.OpenAsync(User, Guid.NewGuid(), Ct);

        Assert.False(found);
        Assert.Empty(session.Turns);
        Assert.Null(session.ConversationId);
    }

    [Fact]
    public async Task TheSessionIsNotReadyUntilItKnowsWhatTheMemberMayDo()
    {
        var (session, _, _) = Create(new FakeAsk { Capabilities = new AskCapabilities(HasPlan: true, AiAnswer: false) });
        Assert.False(session.Ready);
        Assert.False(session.Capabilities.HasPlan); // 沒查過之前不能下「沒有方案」的結論：Ready 為假時頁面不畫它

        await session.InitializeAsync(User, Ct);

        Assert.True(session.Ready);
        Assert.Equal(new AskCapabilities(HasPlan: true, AiAnswer: false), session.Capabilities);
    }

    [Fact]
    public async Task EveryChangeIsAnnouncedSoThePageCanRedraw()
    {
        var (session, _, _) = await ReadyAsync();
        var changes = 0;
        session.Changed += () => changes++;

        await session.AskAsync(User, "問題", null, Ct);

        Assert.True(changes >= 2); // 送出時（等結果）與結束時
    }
}