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
        // 正文與引用分開存：引用是結構化的（代號、法規名、網址），不存條文全文。
        Assert.Equal("答 [L1]", recorded.Answer);
        Assert.Equal(ConversationOutcome.Answered, recorded.Turn.Outcome);
        Assert.Equal(new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/law/32"), Assert.Single(recorded.Turn.Citations));
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
        Assert.Equal("2021-01-01", ask.Recorded[0].Turn.AsOf); // 問的是哪一天，跟著問題存下來
    }

    // 拒答是結果，不是錯誤：說明為什麼沒有答案，記進歷史的也是這句話。
    [Theory]
    [InlineData("no_authority", "找不到足以回答這個問題的法條，所以沒有回答。試試換個說法，或換一天。")]
    [InlineData("insufficient_evidence", "找到了相關的法條，但不足以直接回答這個問題。下列條文供參考；試著把問題問得更具體，例如指明行為或情形。")]
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
        var recorded = Assert.Single(ask.Recorded);
        Assert.Equal($"（拒答）{label}", recorded.Answer);
        Assert.Equal(ConversationOutcome.Refused, recorded.Turn.Outcome);
        Assert.Empty(recorded.Turn.Citations);
    }

    // 找到法源、模型判定不足以回答：仍是拒答（沒有回答正文），但條文留在回合上讓頁面列出，歷史也記下出處。
    [Fact]
    public async Task AnInsufficientEvidenceRefusalKeepsTheFoundArticlesAndRecordsTheirCitations()
    {
        var (session, ask, _) = await ReadyAsync(new FakeAsk
        {
            Result = new AskResult(AskMode.Answer, AskStatus.Ok, Answer: new GroundedAnswer(null, [FakeAsk.Source], "insufficient_evidence", "detail")),
        });

        await session.AskAsync(User, "露天燃燒", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal(TurnState.Refused, turn.State);
        Assert.Equal(FakeAsk.Source, Assert.Single(turn.Answer!.Sources));
        var recorded = Assert.Single(ask.Recorded);
        Assert.Equal(ConversationOutcome.Refused, recorded.Turn.Outcome);
        Assert.Equal(new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/law/32"), Assert.Single(recorded.Turn.Citations));
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

    // 等結果的時候，回合上看得到進行到哪一步，每一步都通知頁面重畫。
    [Fact]
    public async Task WhileWaitingTheTurnShowsWhichStepTheAnswerIsAtAndEveryStepIsAnnounced()
    {
        var gate = new TaskCompletionSource();
        var ask = new FakeAsk { Gate = gate };
        ask.StagesBeforeGate.AddRange([new AnswerProgress(AnswerStage.Grounding), new AnswerProgress(AnswerStage.Writing, 3)]);
        var (session, _, _) = await ReadyAsync(ask);
        var changes = 0;
        session.Changed += () => changes++;

        var pending = session.AskAsync(User, "問題", null, Ct);

        var turn = Assert.Single(session.Turns);
        Assert.Equal((TurnState.Pending, new AnswerProgress(AnswerStage.Writing, 3)), (turn.State, turn.Progress));
        Assert.True(changes >= 3); // 送出、兩個步驟
        gate.SetResult();
        await pending;
        Assert.Equal(TurnState.Answered, turn.State);
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

    // 側欄點進去接續：問題的日期、引用與「可以當前文」的回答都從存下來的結構重建，下一題帶著它們問。
    [Fact]
    public async Task OpeningAStoredConversationRestoresItsDatesAndCitationsAndItsMemoryForTheNextQuestion()
    {
        var id = Guid.NewGuid();
        var at = DateTimeOffset.UtcNow;
        var citation = new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/law/32");
        var (session, ask, _) = await ReadyAsync(new FakeAsk
        {
            Detail = new ConversationDetail(id, "露天燃燒如何處罰", at,
            [
                new ConversationMessageView("user", "露天燃燒如何處罰", at, AsOf: "2021-01-01"),
                new ConversationMessageView("assistant", "不得露天燃燒 [L1]。", at, [citation], Outcome: ConversationOutcome.Answered),
                new ConversationMessageView("user", "怎麼辦", at),
                new ConversationMessageView("assistant", "（拒答）原因", at, Outcome: ConversationOutcome.Refused),
            ]),
        });

        Assert.True(await session.OpenAsync(User, id, Ct));

        Assert.Equal(["2021-01-01 有效", "現行"], session.Turns.Select(t => t.AsOfLabel));
        Assert.Equal(
            "不得露天燃燒 [L1]。\n[L1] 空氣污染防制法 第 32 條（2021-01-01 有效） https://example.test/law/32",
            session.Turns[0].StoredAnswer);

        await session.AskAsync(User, "那罰款多少", null, Ct);

        // 回答的代號換成法規名；拒答的只留問題（說明文字不是答案）。
        Assert.Equal(
            [("user", "露天燃燒如何處罰"), ("assistant", "不得露天燃燒 （空氣污染防制法 第 32 條）。"), ("user", "怎麼辦")],
            Assert.Single(ask.Histories).Select(m => (m.Role.Value, m.Text)));
        Assert.Equal(id, ask.Recorded[0].Conversation); // 接在同一串後面
    }

    // 前文是「這串對話前面有結果的回合」：第一題沒有；失敗的回合沒有可接續的答案，不算。
    [Fact]
    public async Task EachQuestionCarriesTheEarlierAnsweredTurnsAsMemoryWithCodesReplacedByLawNames()
    {
        var (session, ask, _) = await ReadyAsync();

        await session.AskAsync(User, "第一題", null, Ct);
        await session.AskAsync(User, "第二題", null, Ct);
        ask.Result = new AskResult(AskMode.Answer, AskStatus.Unavailable);
        await session.AskAsync(User, "壞掉的一題", null, Ct);
        ask.Result = new AskResult(AskMode.Answer, AskStatus.Ok, Answer: new GroundedAnswer("再答 [L1]", [FakeAsk.Source]));
        await session.AskAsync(User, "第四題", null, Ct);

        Assert.Empty(ask.Histories[0]);
        Assert.Equal(
            [("user", "第一題"), ("assistant", "答 （空氣污染防制法 第 32 條）")],
            ask.Histories[1].Select(m => (m.Role.Value, m.Text)));
        Assert.Equal(
            [("user", "第一題"), ("assistant", "答 （空氣污染防制法 第 32 條）"), ("user", "第二題"), ("assistant", "答 （空氣污染防制法 第 32 條）")],
            ask.Histories[3].Select(m => (m.Role.Value, m.Text))); // 壞掉的那題不在裡面
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