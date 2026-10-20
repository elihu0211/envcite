using System.Globalization;

using EnvCite.Ontology.V1;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>公開的推論契約與內部 gRPC 契約之間的對映；列舉不認得時視為契約破壞，不猜。</summary>
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
        foreach (var fact in r.Facts ?? [])
        {
            if (string.IsNullOrWhiteSpace(fact.Concept))
            {
                throw new ArgumentException("every fact needs a concept.");
            }

            var f = new Fact { Concept = fact.Concept };
            foreach (var number in fact.Numbers ?? [])
            {
                if (!decimal.TryParse(number, NumberStyles.Number, CultureInfo.InvariantCulture, out _))
                {
                    throw new ArgumentException($"'{number}' is not a decimal number (concept '{fact.Concept}').");
                }

                f.Values.Add(new FactValue { Number = new EnvCite.Ontology.V1.Decimal { Value = number } });
            }

            foreach (var flag in fact.Flags ?? [])
            {
                f.Values.Add(new FactValue { Flag = flag });
            }

            foreach (var concept in fact.Concepts ?? [])
            {
                if (string.IsNullOrWhiteSpace(concept))
                {
                    throw new ArgumentException($"a concept value of fact '{fact.Concept}' is blank.");
                }

                f.Values.Add(new FactValue { Concept = concept });
            }

            if (f.Values.Count == 0)
            {
                throw new ArgumentException($"fact '{fact.Concept}' has no values.");
            }

            request.Facts.Add(f);
        }

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
            request.AdministrativePenaltyFacts = p;
        }

        return request;
    }

    public static ReasoningResultDto ToDto(EvaluateResponse r) =>
        new(ToDto(r.AtDecision ?? new Outcome()), [.. r.Earlier.Select(e => new EarlierOutcomeDto(e.FirstDay, ToDto(e.Outcome ?? new Outcome())))]);

    private static OutcomeDto ToDto(Outcome o) => ToDto(o, o.General ?? new GeneralPart());

    // 沒設定的訊息欄位在 C# 是 null（proto3 的空訊息不上線路），所以總則要先補成空的。
    private static OutcomeDto ToDto(Outcome o, GeneralPart general) => new(
        [.. o.Verdicts.Select(v => new StatementVerdictDto(v.StatementId, Map(v.Verdict)))],
        [.. o.Penalties.Select(p => new PenaltyCandidateDto(
            p.StatementId, p.Punishes, Map(p.Effect),
            p.HasMinAmount ? p.MinAmount : null, p.HasMaxAmount ? p.MaxAmount : null,
            p.Conditional, p.PerOccasion, p.Discretionary))],
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
            [.. general.TimeBarred]),
        [.. o.Sources.Select(s => new StatementSourceDto(s.StatementId, s.VersionIri))],
        [.. o.UncoveredLawIds],
        [.. o.DefinitionsUsed.Select(d => new DefinitionUseDto(
            d.ConditionId, d.DefinitionId, d.VersionIri, d.Term, [.. d.Members]))]);

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
        _ => throw Unknown(e),
    };

    private static ProblemKindDto Map(ProblemKind k) => k switch
    {
        ProblemKind.ContradictoryFact => ProblemKindDto.ContradictoryFact,
        ProblemKind.UnresolvedDelegation => ProblemKindDto.UnresolvedDelegation,
        ProblemKind.UnsupportedCondition => ProblemKindDto.UnsupportedCondition,
        ProblemKind.BadDate => ProblemKindDto.BadDate,
        _ => throw Unknown(k),
    };

    private static OverrideBasisDto Map(OverrideBasis b) => b switch
    {
        OverrideBasis.Hierarchy => OverrideBasisDto.Hierarchy,
        OverrideBasis.LexSpecialis => OverrideBasisDto.LexSpecialis,
        OverrideBasis.LexPosterior => OverrideBasisDto.LexPosterior,
        OverrideBasis.Exception => OverrideBasisDto.Exception,
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