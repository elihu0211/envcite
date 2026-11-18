using EnvCite.Canon.V1;

using Grpc.Core;


namespace EnvCite.Platform.Modules.Canon;

/// <summary>
/// 問句的案件事實與推論（<see cref="ICaseReasoner"/>）：包 canon 的 <c>ExtractFacts</c> 與 <c>Evaluate</c>。
/// 抽取與推論的失敗（沒設定抽取模型、逾時、容量用盡）一律是 <see cref="OutcomeStatus.Unavailable"/>，呼叫端退回沒有推論的做法。
/// 問句與事實是客戶的案件內容：這裡不記日誌。
/// </summary>
internal sealed class CaseReasoner(
    ReasoningService.ReasoningServiceClient reasoning,
    IOptions<CanonOptions> options,
    IUsageRecorder usage) : ICaseReasoner
{
    public const string ExtractOperation = "reasoning.extract_facts";

    public async Task<Outcome<IReadOnlyList<CaseFact>>> ExtractFactsAsync(string question, CancellationToken cancellationToken)
    {
        var called = await CallAsync(async () =>
        {
            var response = await reasoning.ExtractFactsAsync(
                new ExtractFactsRequest { Question = question },
                deadline: DateTime.UtcNow.Add(options.Value.ExtractTimeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(ExtractOperation, 1, cancellationToken);
            return response;
        });
        if (!called.IsOk)
            return called.ForwardError<IReadOnlyList<CaseFact>>();

        List<CaseFact> facts = [];
        foreach (var fact in called.Value!.Facts)
        {
            // canon 保證每項事實都有值；沒有值是契約被破壞，整批不採用，與暫時不可用同樣處理。
            if (Map(fact) is not { } mapped)
                return Outcomes.Unavailable<IReadOnlyList<CaseFact>>(OutcomeCodes.CanonUnavailable);

            facts.Add(mapped);
        }

        return Outcomes.Ok<IReadOnlyList<CaseFact>>(facts);
    }

    public async Task<Outcome<CaseConclusions>> EvaluateAsync(
        IReadOnlyList<CaseFact> facts, string asOf, CancellationToken cancellationToken) =>
        await CallAsync(async () =>
        {
            var request = new EvaluateReasoningRequest(
                ConductOn: asOf, DecidedOn: asOf, Facts: [.. facts.Select(Map)], AdministrativePenalty: null,
                ConductEndedOn: null, ResultOn: null);
            var response = await reasoning.EvaluateAsync(
                ReasoningMapper.ToGrpc(request),
                deadline: DateTime.UtcNow.Add(options.Value.ReasoningTimeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(ReasoningEndpoints.EvaluateOperation, 1, cancellationToken);
            return Conclusions(ReasoningMapper.ToDto(response).AtDecision);
        });

    /// <summary>旗標或數值的事實；沒有值（或是這裡不用的值概念）是 null。</summary>
    internal static CaseFact? Map(ExtractedFact fact) => fact.Value?.ValueCase switch
    {
        FactValue.ValueOneofCase.Flag => new CaseFact(fact.Concept, fact.Name, fact.Value.Flag, null, fact.Quote),
        FactValue.ValueOneofCase.Number => new CaseFact(fact.Concept, fact.Name, null, fact.Value.Number.Value, fact.Quote),
        _ => null,
    };

    private static FactDto Map(CaseFact fact) =>
        fact.Flag is { } flag
            ? new FactDto(fact.Concept, null, [flag])
            : new FactDto(fact.Concept, [fact.Number ?? throw new InvalidOperationException("The fact has neither a flag nor a number.")], null);

    /// <summary>結論只取裁處時之法律（行為時與裁處時同日，沒有「更早」的一組）；每則陳述一則，連同它所讀條文的網址。</summary>
    internal static CaseConclusions Conclusions(OutcomeDto outcome)
    {
        var uri = outcome.Sources.ToDictionary(s => s.StatementId, s => s.Uri);
        var missing = outcome.Missing.ToLookup(g => g.StatementId, g => g.Concept);
        var assessment = outcome.NeedsAssessment.ToLookup(g => g.StatementId, g => g.Concept);
        var penalties = outcome.Penalties.ToLookup(
            p => p.Punishes,
            p => new PenaltyRange(p.Effect.ToString(), p.MinAmount, p.MaxAmount, p.Conditional, p.Discretionary));
        return new CaseConclusions(
        [
            .. outcome.Verdicts
                .Where(v => uri.ContainsKey(v.StatementId))
                .Select(v => new StatementConclusion(
                    uri[v.StatementId],
                    Map(v.Verdict),
                    [.. missing[v.StatementId].Distinct(StringComparer.Ordinal)],
                    [.. assessment[v.StatementId].Distinct(StringComparer.Ordinal)],
                    [.. penalties[v.StatementId]])),
        ]);
    }

    internal static ConclusionVerdict Map(VerdictDto verdict) => verdict switch
    {
        VerdictDto.Violation => ConclusionVerdict.Satisfied,
        VerdictDto.Compliant => ConclusionVerdict.NotSatisfied,
        VerdictDto.ConditionalViolation => ConclusionVerdict.ConditionallySatisfied,
        VerdictDto.ConditionalCompliant => ConclusionVerdict.ConditionallyNotSatisfied,
        VerdictDto.Undetermined => ConclusionVerdict.Undetermined,
        VerdictDto.Excluded => ConclusionVerdict.Excluded,
        VerdictDto.NotApplicable => ConclusionVerdict.NotApplicable,
        _ => throw new ArgumentOutOfRangeException(nameof(verdict), verdict, null),
    };

    private static async Task<Outcome<T>> CallAsync<T>(Func<Task<T>> call)
    {
        try
        {
            return await PlatformGrpcClient.CallAsync(OutcomeCodes.CanonUnavailable, OutcomeCodes.ReasoningBusy, call);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.FailedPrecondition or StatusCode.Unimplemented)
        {
            // canon 沒有設定抽取模型，或還是不認得這個呼叫的舊版（平台先於 canon 部署）：功能沒開，與暫時不可用同樣處理。
            return Outcomes.Unavailable<T>(OutcomeCodes.CanonUnavailable);
        }
    }
}