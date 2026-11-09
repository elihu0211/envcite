using EnvCite.Ontology.V2;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 公開的推論契約與內部 gRPC 契約之間的對映；列舉不認得時視為契約破壞，不猜。只轉譯，不驗證：
/// 事實與日期的檢查由 ontology 一處負責，錯誤以 <c>InvalidArgument</c> 回來。
/// </summary>
internal static class ReasoningMapper
{
    public static EvaluateRequest ToGrpc(EvaluateReasoningRequest r)
    {
        var request = new EvaluateRequest
        {
            ConductOn = r.ConductOn ?? string.Empty,
            DecidedOn = r.DecidedOn ?? string.Empty,
            ConductEndedOn = r.ConductEndedOn ?? string.Empty,
            ResultOn = r.ResultOn ?? string.Empty,
        };
        // 缺的值轉成空字串：proto 的字串不收 null，缺席由 ontology 回報為欄位錯誤。
        foreach (var fact in r.Facts ?? [])
        {
            var f = new Fact
            {
                Concept = fact.Concept ?? string.Empty,
                MeasurementMethod = fact.MeasurementMethod ?? string.Empty,
                AveragingPeriod = fact.AveragingPeriod ?? string.Empty,
            };
            f.Values.AddRange((fact.Numbers ?? []).Select(n => new FactValue { Number = Number(n) }));
            f.Values.AddRange((fact.Flags ?? []).Select(flag => new FactValue { Flag = flag }));
            f.Values.AddRange((fact.Concepts ?? []).Select(c => new FactValue { Concept = c ?? string.Empty }));
            request.Facts.Add(f);
        }

        request.PermitLimits.AddRange((r.PermitLimits ?? []).Select(l => new PermitLimit
        {
            Permit = l.Permit ?? string.Empty,
            Concept = l.Concept ?? string.Empty,
            Value = Number(l.Value),
        }));

        request.ActorBases.AddRange((r.ActorBases ?? []).Select(Map));

        if (r.AdministrativePenalty is { } a)
        {
            var p = new AdministrativePenaltyFacts();
            if (a.Intent is { } intent) { p.Intent = intent; }
            if (a.Negligence is { } negligence) { p.Negligence = negligence; }
            if (a.AgentFault is { } agentFault) { p.AgentFault = agentFault; }
            if (a.Age is { } age) { p.Age = age; }
            if (a.Incapable is { } incapable) { p.Incapable = incapable; }
            if (a.SelfInduced is { } selfInduced) { p.SelfInduced = selfInduced; }
            if (a.PursuantToLaw is { } pursuantToLaw) { p.PursuantToLaw = pursuantToLaw; }
            if (a.SuperiorOrder is { } superiorOrder) { p.SuperiorOrder = superiorOrder; }
            if (a.KnewIllegal is { } knewIllegal) { p.KnewIllegal = knewIllegal; }
            if (a.StatedOpinion is { } statedOpinion) { p.StatedOpinion = statedOpinion; }
            if (a.SelfDefense is { } selfDefense) { p.SelfDefense = selfDefense; }
            if (a.DefenseExcessive is { } defenseExcessive) { p.DefenseExcessive = defenseExcessive; }
            if (a.Necessity is { } necessity) { p.Necessity = necessity; }
            if (a.NecessityExcessive is { } necessityExcessive) { p.NecessityExcessive = necessityExcessive; }
            if (a.CapacityReduced is { } capacityReduced) { p.CapacityReduced = capacityReduced; }
            if (a.IgnoranceOfLaw is { } ignorance) { p.IgnoranceOfLaw = ignorance; }
            if (a.CriminalOffense is { } criminalOffense) { p.CriminalOffense = criminalOffense; }
            if (a.CourtConfiscated is { } courtConfiscated) { p.CourtConfiscated = courtConfiscated; }
            p.CriminalClearedOn = a.CriminalClearedOn ?? string.Empty;
            if (a.CriminalPayment is { } payment) { p.CriminalPayment = Number(payment); }
            if (a.CommunityServiceHours is { } hours) { p.CommunityServiceHours = Number(hours); }
            if (a.MinimumHourlyWage is { } wage) { p.MinimumHourlyWage = Number(wage); }
            if (a.GainedBenefit is { } benefit) { p.GainedBenefit = Number(benefit); }
            request.AdministrativePenaltyFacts = p;
        }

        return request;
    }

    private static EnvCite.Ontology.V2.Decimal Number(string? value) => new() { Value = value ?? string.Empty };

    public static ReasoningResultDto ToDto(EvaluateResponse r) =>
        new(ToDto(r.AtDecision ?? new Outcome()), [.. r.Earlier.Select(e => new EarlierOutcomeDto(e.FirstDay, ToDto(e.Outcome ?? new Outcome())))]);

    private static OutcomeDto ToDto(Outcome o) => ToDto(o, o.General ?? new GeneralPart());

    // 沒設定的訊息欄位在 C# 是 null（proto3 的空訊息不上線路），所以總則要先補成空的。
    private static OutcomeDto ToDto(Outcome o, GeneralPart general) => new(
        [.. o.Verdicts.Select(v => new StatementVerdictDto(v.StatementId, Map(v.Verdict)))],
        [.. o.Penalties.Select(p => new PenaltyCandidateDto(
            p.StatementId, p.Punishes, Map(p.Effect),
            p.HasMinAmount ? p.MinAmount : null, p.HasMaxAmount ? p.MaxAmount : null,
            p.Conditional, p.PerOccasion, p.Discretionary,
            [.. p.TriggeredBy.Select(t => new SanctionTriggerDto(t.StatementId, Map(t.Kind)))],
            [.. p.SubjectBases.Select(Map)]))],
        o.OneAct is { } one ? new FineIntervalDto(one.MinAmount, one.MaxAmount) : null,
        [.. o.Missing.Select(g => new GapDto(g.StatementId, g.Concept))],
        [.. o.NeedsAssessment.Select(g => new GapDto(g.StatementId, g.Concept))],
        [.. o.AssessmentUsed.Select(g => new GapDto(g.StatementId, g.Concept))],
        [.. o.Problems.Select(p => new ProblemDto(
            Map(p.Kind), p.SubjectCase == Problem.SubjectOneofCase.Concept ? p.Concept : null,
            p.SubjectCase == Problem.SubjectOneofCase.ConditionId ? p.ConditionId : null))],
        [.. o.UnresolvedConflicts.Select(c => new StatementPairDto(c.First, c.Second))],
        [.. o.Defeated.Select(d => new DefeatDto(d.Loser, d.Winner, Map(d.Basis)))],
        [.. o.UnresolvedPenaltyOverlaps.Select(c => new StatementPairDto(c.First, c.Second))],
        [.. o.PenaltyDefeated.Select(d => new DefeatDto(d.Loser, d.Winner, Map(d.Basis)))],
        [.. o.Trace.Select(t => new TraceEntryDto(
            t.ConditionId, t.StatementId, t.Concept, t.Fact?.Value ?? string.Empty,
            t.Threshold?.Value ?? string.Empty, Map(t.Role)))],
        [.. o.LimitSources.OrderBy(kv => kv.Key).Select(kv => new LimitSourceDto(kv.Key, kv.Value))],
        new GeneralPartDto(
            [.. general.Unpunished.Select(u => new PenaltyExcuseDto(u.PenaltyStatementId, Map(u.Excuse)))],
            [.. general.Mitigation.Select(m => new PenaltyMitigationDto(m.PenaltyStatementId, Map(m.Mitigation)))],
            [.. general.PresumedAgentFault],
            [.. general.TimeBarred],
            [.. general.Reduced.Select(f => new ReducedFineDto(
                f.PenaltyStatementId, Map(f.Mitigation), Map(f.Share),
                f.HasMinAmount ? f.MinAmount : null, f.HasMaxAmount ? f.MaxAmount : null))],
            [.. general.RaisedCaps.Select(c => new RaisedCapDto(c.PenaltyStatementId, c.MaxAmount?.Value ?? string.Empty))],
            [.. general.CriminalDeductions.Select(d => new CriminalDeductionDto(d.PenaltyStatementId, d.Amount?.Value ?? string.Empty))]),
        [.. o.Sources.Select(s => new StatementSourceDto(s.StatementId, s.VersionIri))],
        [.. o.UncoveredLawIdentifiers.Select(LawIdentifiers.ToDto)],
        [.. o.DefinitionsUsed.Select(d => new DefinitionUseDto(
            d.ConditionId, d.DefinitionId, d.VersionIri, d.Term, [.. d.Members]))],
        [.. o.Guidance.Select(g => new GuidanceDto(
            g.Issuer, g.DocumentNo, g.IssuedOn, Map(g.Kind), g.Subject, [.. g.StatementIds],
            NullIfEmpty(g.PublishedIn), NullIfEmpty(g.SourceUrl)))],
        [.. o.MissingPermitLimits.Select(g => new PermitGapDto(g.StatementId, g.Permit, g.Concept))],
        [.. o.SubjectMismatch],
        [.. o.Invalidated.Select(i => new InvalidatedStatementDto(i.StatementId, i.VersionIri, i.RulingNo, i.InvalidFrom, i.Partial))],
        [.. o.DelegationBases.Select(b => new DelegationBasisDto(b.StatementId, b.Rooted, [.. b.Links.Select(Map)]))]);

    // 空字串是 proto3 的「沒有」，對外寫成 null；gap 的零值是「這一環成立」，不是契約之外的值。
    private static DelegationLinkDto Map(DelegationLink l) => new(
        LawIdentifiers.ToDto(l.CommandIdentifier), NullIfEmpty(l.CitedLawName), NullIfEmpty(l.CitedPath),
        l.Identifier is null ? null : LawIdentifiers.ToDto(l.Identifier),
        NullIfEmpty(l.VersionIri), l.Gap == DelegationGap.Unspecified ? null : Map(l.Gap));

    private static string? NullIfEmpty(string value) => value.Length > 0 ? value : null;

    private static DelegationGapDto Map(DelegationGap g) => g switch
    {
        DelegationGap.NoCitation => DelegationGapDto.NoCitation,
        DelegationGap.UnresolvedLaw => DelegationGapDto.UnresolvedLaw,
        DelegationGap.LawNotInForce => DelegationGapDto.LawNotInForce,
        DelegationGap.LawInvalidated => DelegationGapDto.LawInvalidated,
        DelegationGap.Cycle => DelegationGapDto.Cycle,
        _ => throw Unknown(g),
    };

    private static ReductionShareDto Map(ReductionShare s) => s switch
    {
        ReductionShare.Half => ReductionShareDto.Half,
        ReductionShare.Third => ReductionShareDto.Third,
        _ => throw Unknown(s),
    };

    private static VerdictDto Map(Verdict v) => v switch
    {
        Verdict.Violation => VerdictDto.Violation,
        Verdict.Compliant => VerdictDto.Compliant,
        Verdict.ConditionalViolation => VerdictDto.ConditionalViolation,
        Verdict.ConditionalCompliant => VerdictDto.ConditionalCompliant,
        Verdict.Undetermined => VerdictDto.Undetermined,
        Verdict.Excluded => VerdictDto.Excluded,
        Verdict.NotApplicable => VerdictDto.NotApplicable,
        _ => throw Unknown(v),
    };

    private static LegalEffectDto Map(LegalEffect e) => e switch
    {
        LegalEffect.Fine => LegalEffectDto.Fine,
        LegalEffect.Confiscation => LegalEffectDto.Confiscation,
        LegalEffect.Restriction => LegalEffectDto.Restriction,
        LegalEffect.Deprivation => LegalEffectDto.Deprivation,
        LegalEffect.Reputation => LegalEffectDto.Reputation,
        LegalEffect.Warning => LegalEffectDto.Warning,
        LegalEffect.Order => LegalEffectDto.Order,
        LegalEffect.Disgorgement => LegalEffectDto.Disgorgement,
        _ => throw Unknown(e),
    };

    private static LiabilityBasisDto Map(LiabilityBasis b) => b switch
    {
        LiabilityBasis.Act => LiabilityBasisDto.Act,
        LiabilityBasis.State => LiabilityBasisDto.State,
        LiabilityBasis.Representative => LiabilityBasisDto.Representative,
        _ => throw Unknown(b),
    };

    private static LiabilityBasis Map(LiabilityBasisDto b) => b switch
    {
        LiabilityBasisDto.Act => LiabilityBasis.Act,
        LiabilityBasisDto.State => LiabilityBasis.State,
        LiabilityBasisDto.Representative => LiabilityBasis.Representative,
        _ => throw new ArgumentOutOfRangeException(nameof(b), b, null),
    };

    private static TriggerKindDto Map(TriggerKind k) => k switch
    {
        TriggerKind.OrderLapsed => TriggerKindDto.OrderLapsed,
        TriggerKind.SanctionImposed => TriggerKindDto.SanctionImposed,
        _ => throw Unknown(k),
    };

    private static ProblemKindDto Map(ProblemKind k) => k switch
    {
        ProblemKind.ContradictoryFact => ProblemKindDto.ContradictoryFact,
        ProblemKind.UnresolvedDelegation => ProblemKindDto.UnresolvedDelegation,
        ProblemKind.BadDate => ProblemKindDto.BadDate,
        ProblemKind.MeasurementMismatch => ProblemKindDto.MeasurementMismatch,
        ProblemKind.MeasurementUnlabeled => ProblemKindDto.MeasurementUnlabeled,
        ProblemKind.InvalidatedDelegation => ProblemKindDto.InvalidatedDelegation,
        _ => throw Unknown(k),
    };

    private static GuidanceKindDto Map(GuidanceKind k) => k switch
    {
        GuidanceKind.Interpretation => GuidanceKindDto.Interpretation,
        GuidanceKind.DiscretionStandard => GuidanceKindDto.DiscretionStandard,
        GuidanceKind.Operational => GuidanceKindDto.Operational,
        _ => throw Unknown(k),
    };

    private static OverrideBasisDto Map(OverrideBasis b) => b switch
    {
        OverrideBasis.Hierarchy => OverrideBasisDto.Hierarchy,
        OverrideBasis.LexSpecialis => OverrideBasisDto.LexSpecialis,
        OverrideBasis.LexPosterior => OverrideBasisDto.LexPosterior,
        _ => throw Unknown(b),
    };

    private static ConditionRoleDto Map(ConditionRole r) => r switch
    {
        ConditionRole.Scope => ConditionRoleDto.Scope,
        ConditionRole.Duty => ConditionRoleDto.Duty,
        ConditionRole.Limit => ConditionRoleDto.Limit,
        _ => throw Unknown(r),
    };

    private static ExcuseDto Map(Excuse e) => e switch
    {
        Excuse.NoFault => ExcuseDto.NoFault,
        Excuse.UnderFourteen => ExcuseDto.UnderFourteen,
        Excuse.Incapable => ExcuseDto.Incapable,
        Excuse.PursuantToLaw => ExcuseDto.PursuantToLaw,
        Excuse.SuperiorOrder => ExcuseDto.SuperiorOrder,
        Excuse.SelfDefense => ExcuseDto.SelfDefense,
        Excuse.Necessity => ExcuseDto.Necessity,
        Excuse.CriminalPriority => ExcuseDto.CriminalPriority,
        _ => throw Unknown(e),
    };

    private static MitigationDto Map(Mitigation m) => m switch
    {
        Mitigation.Youth => MitigationDto.Youth,
        Mitigation.ReducedCapacity => MitigationDto.ReducedCapacity,
        Mitigation.Ignorance => MitigationDto.Ignorance,
        Mitigation.ExcessiveDefense => MitigationDto.ExcessiveDefense,
        Mitigation.ExcessiveNecessity => MitigationDto.ExcessiveNecessity,
        _ => throw Unknown(m),
    };

    private static InvalidOperationException Unknown<T>(T value) where T : struct, Enum =>
        new($"the ontology service returned {typeof(T).Name} {value}, which this platform does not know.");
}