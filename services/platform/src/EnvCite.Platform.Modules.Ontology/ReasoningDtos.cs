using System.Text.Json.Serialization;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 案件事實中的一個概念。同一概念可有多個值；兩個不同的值是矛盾，會被回報為問題，不會被擇一。
/// </summary>
/// <param name="Concept">概念代碼（受控詞彙），不可以 <c>ap_</c> 開頭；行政罰法總則的事實放在 <c>administrativePenalty</c>。</param>
/// <param name="Numbers">數值，十進位文字（如 <c>"100"</c>、<c>"0.5"</c>），不用浮點數。</param>
/// <param name="Flags">是／否的事實。</param>
/// <param name="Concepts">概念取某個值概念（例如行業屬於某一類），值為受控概念的代碼；只與以概念比較的要件對得上，
/// 「屬於」只依該法現行已確認的用語定義判定，沒有定義涵蓋視為不屬於。</param>
public sealed record FactDto(
    string Concept, IReadOnlyList<string>? Numbers, IReadOnlyList<bool>? Flags,
    IReadOnlyList<string>? Concepts = null);

/// <summary>
/// 行政罰法總則所依的事實。<b>省略欄位是「未知」，不是「否」</b>：條文有些規定只在已知為否時才適用，
/// 有些只在已知為是時才適用。
/// </summary>
/// <param name="Intent">故意。</param>
/// <param name="Negligence">過失。</param>
/// <param name="AgentFault">代表人、管理人、受僱人等之故意過失（第 7 條第 2 項）。</param>
/// <param name="Age">行為時年齡（歲）。</param>
/// <param name="Incapable">行為時因精神障礙或其他心智缺陷，不能辨識其行為違法或欠缺依其辨識而行為之能力（第 9 條第 3 項）。</param>
/// <param name="SelfInduced">前項原因因故意或過失自行招致（第 9 條第 5 項）。</param>
/// <param name="PursuantToLaw">依法令之行為（第 11 條第 1 項）。</param>
/// <param name="SuperiorOrder">依所屬上級公務員職務命令之行為（第 11 條第 2 項）。</param>
/// <param name="KnewIllegal">明知職務命令違法。</param>
/// <param name="StatedOpinion">已依法定程序向該上級公務員陳述意見。</param>
/// <param name="SelfDefense">正當防衛（第 12 條）。</param>
/// <param name="DefenseExcessive">防衛行為過當。</param>
/// <param name="Necessity">緊急避難（第 13 條）。</param>
/// <param name="NecessityExcessive">避難行為過當。</param>
/// <param name="CapacityReduced">辨識能力顯著減低（第 9 條第 4 項）。</param>
/// <param name="IgnoranceOfLaw">不知法規（第 8 條）。</param>
public sealed record AdministrativePenaltyFactsDto(
    bool? Intent, bool? Negligence, bool? AgentFault, int? Age, bool? Incapable, bool? SelfInduced,
    bool? PursuantToLaw, bool? SuperiorOrder, bool? KnewIllegal, bool? StatedOpinion, bool? SelfDefense,
    bool? DefenseExcessive, bool? Necessity, bool? NecessityExcessive, bool? CapacityReduced, bool? IgnoranceOfLaw);

/// <param name="ConductOn">行為日（<c>YYYY-MM-DD</c>）；必填。</param>
/// <param name="DecidedOn">裁處日（<c>YYYY-MM-DD</c>）；必填，不早於行為日。</param>
/// <param name="Facts">案件事實，依概念代碼。</param>
/// <param name="AdministrativePenalty">行政罰法總則所依的事實；省略表示全部未知。</param>
/// <param name="ConductEndedOn">行為終了日；起算行政罰法第 27 條的三年裁處權時效。省略則不套用時效。</param>
/// <param name="ResultOn">結果發生日，晚於行為終了時才給。</param>
public sealed record EvaluateReasoningRequest(
    string? ConductOn, string? DecidedOn, IReadOnlyList<FactDto>? Facts,
    AdministrativePenaltyFactsDto? AdministrativePenalty, string? ConductEndedOn, string? ResultOn);

[JsonConverter(typeof(JsonStringEnumConverter<VerdictDto>))]
public enum VerdictDto
{
    Violation,
    Compliant,
    /// <summary>僅在一個尚未決定的但書不成立時才違反。</summary>
    ConditionalViolation,
    ConditionalCompliant,
    /// <summary>事實不足，無法判定；見 <c>missing</c> 與 <c>needsAssessment</c>。</summary>
    Undetermined,
    /// <summary>被成立的但書排除。</summary>
    Excluded,
    NotApplicable,
}

[JsonConverter(typeof(JsonStringEnumConverter<LegalEffectDto>))]
public enum LegalEffectDto
{
    Fine,
    Confiscation,
    Restriction,
    Deprivation,
    Reputation,
    Warning,
    /// <summary>命令（改善命令等預防性、非裁罰性不利處分）；不是行政罰，行政罰法總則不適用。</summary>
    Order,
}

[JsonConverter(typeof(JsonStringEnumConverter<ProblemKindDto>))]
public enum ProblemKindDto
{
    /// <summary>同一概念有兩個不同的值：該概念視同未知。</summary>
    ContradictoryFact,
    /// <summary>授權命令的限值在該日解析不到。</summary>
    UnresolvedDelegation,
    /// <summary>推論不比較的要件。</summary>
    UnsupportedCondition,
    BadDate,
}

[JsonConverter(typeof(JsonStringEnumConverter<OverrideBasisDto>))]
public enum OverrideBasisDto
{
    /// <summary>位階（中央法規標準法第 11 條）。</summary>
    Hierarchy,
    /// <summary>特別規定（同法第 16 條）。</summary>
    LexSpecialis,
    LexPosterior,
    Exception,
}

[JsonConverter(typeof(JsonStringEnumConverter<ConditionRoleDto>))]
public enum ConditionRoleDto
{
    Scope,
    Duty,
    Limit,
}

[JsonConverter(typeof(JsonStringEnumConverter<ExcuseDto>))]
public enum ExcuseDto
{
    /// <summary>無故意過失（第 7 條）。</summary>
    NoFault,
    /// <summary>未滿十四歲（第 9 條）。</summary>
    UnderFourteen,
    /// <summary>精神障礙或其他心智缺陷，不能辨識（第 9 條）。</summary>
    Incapable,
    PursuantToLaw,
    SuperiorOrder,
    SelfDefense,
    Necessity,
}

[JsonConverter(typeof(JsonStringEnumConverter<MitigationDto>))]
public enum MitigationDto
{
    Youth,
    ReducedCapacity,
    Ignorance,
    ExcessiveDefense,
    ExcessiveNecessity,
}

public sealed record StatementVerdictDto(long StatementId, VerdictDto Verdict);

/// <param name="StatementId">罰則陳述。</param>
/// <param name="Punishes">它所處罰的義務陳述。</param>
/// <param name="MinAmount">法定最低額（元）；法律沒寫則省略。</param>
/// <param name="MaxAmount">法定最高額（元）；法律沒寫則省略。</param>
/// <param name="Conditional">罰則自己的要件，或所處罰義務的結論尚未決定。</param>
/// <param name="PerOccasion">按次處罰。</param>
/// <param name="Discretionary">條文寫「得」：效果裁量。系統只列候選，不選額度。</param>
public sealed record PenaltyCandidateDto(
    long StatementId, long Punishes, LegalEffectDto Effect, long? MinAmount, long? MaxAmount,
    bool Conditional, bool PerOccasion, bool Discretionary);

/// <summary>一行為（第 24 條）的罰鍰合併區間（元）。數行為（第 25 條）則是各罰則自己的額度。</summary>
public sealed record FineIntervalDto(long MinAmount, long MaxAmount);

/// <summary>陳述需要的一個概念。</summary>
public sealed record GapDto(long StatementId, string Concept);

/// <param name="Concept">矛盾事實或不支援的要件所涉概念。</param>
/// <param name="ConditionId">授權解析失敗的要件。</param>
public sealed record ProblemDto(ProblemKindDto Kind, string? Concept, long? ConditionId);

public sealed record StatementPairDto(long First, long Second);

public sealed record DefeatDto(long Loser, long Winner, OverrideBasisDto Basis);

/// <param name="Fact">實際比較的事實值（十進位文字）。</param>
/// <param name="Threshold">門檻值（十進位文字）。</param>
public sealed record TraceEntryDto(
    long ConditionId, long StatementId, string Concept, string Fact, string Threshold, ConditionRoleDto Role);

/// <summary>要件的限值來自哪一個授權命令陳述。</summary>
public sealed record LimitSourceDto(long ConditionId, long StatementId);

public sealed record PenaltyExcuseDto(long PenaltyStatementId, ExcuseDto Excuse);

public sealed record PenaltyMitigationDto(long PenaltyStatementId, MitigationDto Mitigation);

/// <summary>行政罰法總則對罰則的標記。排除事由成立者不在 <c>penalties</c>，而在 <c>unpunished</c>。</summary>
/// <param name="PresumedAgentFault">依代表人等之過失推定的罰則。</param>
/// <param name="TimeBarred">三年時效已過的罰則：罰則仍是候選，是否適用時效由呼叫端決定。</param>
public sealed record GeneralPartDto(
    IReadOnlyList<PenaltyExcuseDto> Unpunished, IReadOnlyList<PenaltyMitigationDto> Mitigation,
    IReadOnlyList<long> PresumedAgentFault, IReadOnlyList<long> TimeBarred);

/// <summary>結論所讀的條文版本。</summary>
/// <param name="Uri">條文版本的網址。</param>
public sealed record StatementSourceDto(long StatementId, string Uri);

/// <summary>概念值要件之所以成立或不成立所依的一則用語定義：「甲屬於乙」的依據。</summary>
/// <param name="Uri">定義所讀自的條文版本的網址。</param>
/// <param name="Term">被定義的用語。</param>
/// <param name="Members">定義明文涵蓋的成員。</param>
public sealed record DefinitionUseDto(
    long ConditionId, long DefinitionId, string Uri, string Term, IReadOnlyList<string> Members);

/// <summary>
/// 某一組法律的推論結果。全部是<b>候選</b>與依據：系統不裁量、不選「最有利」、不在沒有明文優先時消解衝突。
/// </summary>
/// <param name="NeedsAssessment">要件是不確定法律概念，須呼叫端判斷；不算缺事實。</param>
/// <param name="AssessmentUsed">結論倚賴了呼叫端對不確定法律概念的判斷。</param>
/// <param name="UncoveredLawIds">當日已制定、但語料沒有當時版本的法規（pcode）；其規定不在結果裡，
/// 不代表當時沒有這些規定。</param>
/// <param name="DefinitionsUsed">概念值要件所依的用語定義；沒有定義涵蓋而判為不屬於的，沒有定義可指，不在此。</param>
public sealed record OutcomeDto(
    IReadOnlyList<StatementVerdictDto> Verdicts, IReadOnlyList<PenaltyCandidateDto> Penalties,
    FineIntervalDto? OneAct, IReadOnlyList<GapDto> Missing, IReadOnlyList<GapDto> NeedsAssessment,
    IReadOnlyList<GapDto> AssessmentUsed, IReadOnlyList<ProblemDto> Problems,
    IReadOnlyList<StatementPairDto> UnresolvedConflicts, IReadOnlyList<DefeatDto> Defeated,
    IReadOnlyList<StatementPairDto> UnresolvedPenaltyOverlaps, IReadOnlyList<DefeatDto> PenaltyDefeated,
    IReadOnlyList<TraceEntryDto> Trace, IReadOnlyList<LimitSourceDto> LimitSources, GeneralPartDto General,
    IReadOnlyList<StatementSourceDto> Sources, IReadOnlyList<string> UncoveredLawIds,
    IReadOnlyList<DefinitionUseDto> DefinitionsUsed);

/// <param name="FirstDay">這一組陳述開始適用的第一天（<c>YYYY-MM-DD</c>）。</param>
public sealed record EarlierOutcomeDto(string FirstDay, OutcomeDto Outcome);

/// <param name="AtDecision">裁處時之法律（行政罰法第 5 條的預設）。</param>
/// <param name="Earlier">行為日到裁處日之間、陳述組與裁處時不同的法律，依日期由早到晚。
/// 第 5 條但書的「最有利於受處罰者」不由系統選：各組無法機械地排序。</param>
public sealed record ReasoningResultDto(OutcomeDto AtDecision, IReadOnlyList<EarlierOutcomeDto> Earlier);