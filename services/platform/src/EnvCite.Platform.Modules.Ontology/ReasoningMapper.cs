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
                Unit = fact.Unit ?? string.Empty,
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
            if (a.FactMistake is { } factMistake) { p.FactMistake = factMistake; }
            if (a.IgnoranceUnavoidable is { } ignUnavoidable) { p.IgnoranceUnavoidable = ignUnavoidable; }
            if (a.Unexpectable is { } unexpectable) { p.Unexpectable = unexpectable; }
            if (a.PettyMinor is { } pettyMinor) { p.PettyMinor = pettyMinor; }
            request.AdministrativePenaltyFacts = p;
        }

        foreach (var ag in r.ActGroups ?? [])
        {
            var g = new ActGroup { ActId = ag.ActId ?? string.Empty };
            g.StatementIds.AddRange(ag.StatementIds);
            request.ActGroups.Add(g);
        }

        if (r.Timeline is { } tl)
        {
            request.Timeline = new PerOccasionTimeline
            {
                CureNoticeServedOn = tl.CureNoticeServedOn ?? string.Empty,
                CureDeadline = tl.CureDeadline ?? string.Empty,
                ExtensionDeadline = tl.ExtensionDeadline ?? string.Empty,
                ReinspectionOn = tl.ReinspectionOn ?? string.Empty,
            };
        }

        foreach (var si in r.ScoringInputs ?? [])
        {
            var item = new ScoringInput
            {
                MatrixCode = si.MatrixCode ?? string.Empty,
                SevereViolation = si.SevereViolation,
                FirstOffenseMinorFacility = si.FirstOffenseMinorFacility,
                CuredBeforeNotice = si.CuredBeforeNotice,
                IsStopWork = si.IsStopWork,
                RepeatWithinYear = si.RepeatWithinYear,
                ActiveBenefit = Number(si.ActiveBenefit ?? "0"),
                PassiveBenefit = Number(si.PassiveBenefit ?? "0"),
                CriminalDeduction = Number(si.CriminalDeduction ?? "0"),
            };
            if (si.MitigationDenominator is { } md) { item.MitigationDenominator = md; }
            foreach (var fv in si.Factors ?? [])
            {
                item.Factors.Add(new ScoringFactorValue { FactorCode = fv.FactorCode ?? string.Empty, Value = Number(fv.Value) });
            }
            request.ScoringInputs.Add(item);
        }

        if (r.DispositionAudit is { } da)
        {
            var audit = new DispositionAuditInput
            {
                TwoTierDefensesAddressed = da.TwoTierDefensesAddressed,
                ScoringMatrixCode = da.ScoringMatrixCode ?? string.Empty,
            };
            if (da.StatedFine is { } sf) { audit.StatedFine = sf; }
            audit.Citations.AddRange(da.Citations.Select(c => new DispositionCitation
            {
                CitationText = c.CitationText ?? string.Empty,
                Role = c.Role ?? string.Empty,
                InForce = c.InForce,
            }));
            audit.Elements.AddRange(da.Elements.Select(e => new DispositionElement
            {
                ElementName = e.ElementName ?? string.Empty,
                Tier = e.Tier ?? string.Empty,
                FactSummary = e.FactSummary ?? string.Empty,
                EvidenceKind = e.EvidenceKind ?? string.Empty,
                Contradicted = e.Contradicted,
            }));
            request.DispositionAudit = audit;
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
            [.. general.Unpunished.Select(u => TryMapLegacyExcuse(u.Excuse) is { } dto ? new PenaltyExcuseDto(u.PenaltyStatementId, dto) : null).OfType<PenaltyExcuseDto>()],
            [.. general.Mitigation.Select(m => new PenaltyMitigationDto(m.PenaltyStatementId, Map(m.Mitigation)))],
            [.. general.PresumedAgentFault],
            [.. general.TimeBarred],
            [.. general.Reduced.Select(f => new ReducedFineDto(
                f.PenaltyStatementId, Map(f.Mitigation), Map(f.Share),
                f.HasMinAmount ? f.MinAmount : null, f.HasMaxAmount ? f.MaxAmount : null))],
            [.. general.RaisedCaps.Select(c => new RaisedCapDto(c.PenaltyStatementId, c.MaxAmount?.Value ?? string.Empty))],
            [.. general.CriminalDeductions.Select(d => new CriminalDeductionDto(d.PenaltyStatementId, d.Amount?.Value ?? string.Empty))],
            [.. general.Justified.Select(u => new TierExcuseDto(u.PenaltyStatementId, u.Excuse.ToString()))],
            [.. general.Excused.Select(u => new TierExcuseDto(u.PenaltyStatementId, u.Excuse.ToString()))]),
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
        [.. o.DelegationBases.Select(b => new DelegationBasisDto(b.StatementId, b.Rooted, [.. b.Links.Select(Map)]))],
        [.. o.OneActGroups.Select(g => new ActGroupFineDto(g.ActId, g.MinAmount, g.MaxAmount))],
        [.. o.SameKindNonFineOverlaps.Select(s => new SameKindNonFineOverlapDto(s.ActId, s.Penalty1, s.Penalty2))],
        [.. o.MutatisApplied.Select(p => new StatementPairDto(p.First, p.Second))],
        [.. o.ScoringResults.Select(MapScoring)],
        o.DispositionAudit is { } auditRes
            ? new DispositionAuditResultDto(
                auditRes.D1CitationsComplete,
                auditRes.D2EvidenceGrounded,
                auditRes.D3DefenseAndBoundsValid,
                [.. auditRes.Findings])
            : null);

    private static ScoringResultDto MapScoring(ScoringResult sr) => new(
        sr.MatrixCode, sr.LawTitle, sr.NormKind, sr.FormulaKind, sr.EquationSummary,
        [.. sr.FactorsApplied.Select(fa => new FactorAppliedDto(
            fa.FactorCode, fa.LabelZho, fa.FactorRole, fa.StatutoryRef,
            fa.Value?.Value, fa.IntervalMin?.Value, fa.IntervalMax?.Value, fa.Missing, fa.NeedsJudgement))],
        sr.RawMin?.Value, sr.RawMax?.Value,
        sr.HasClampedMin ? sr.ClampedMin : null, sr.HasClampedMax ? sr.ClampedMax : null,
        sr.DemeritTotal?.Value, sr.HasDemeritTriggered ? sr.DemeritTriggered : null,
        sr.LectureHoursMin, sr.LectureHoursMax,
        [.. sr.MissingFactors], [.. sr.JudgementFactors], [.. sr.Notes]);

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
        OverrideBasis.MutatisMutandis => OverrideBasisDto.MutatisMutandis,
        OverrideBasis.Subsidiarity => OverrideBasisDto.Subsidiarity,
        OverrideBasis.Absorption => OverrideBasisDto.Absorption,
        _ => throw Unknown(b),
    };

    private static ConditionRoleDto Map(ConditionRole r) => r switch
    {
        ConditionRole.Scope => ConditionRoleDto.Scope,
        ConditionRole.Duty => ConditionRoleDto.Duty,
        ConditionRole.Limit => ConditionRoleDto.Limit,
        _ => throw Unknown(r),
    };

    private static ExcuseDto? TryMapLegacyExcuse(Excuse e) => e switch
    {
        Excuse.NoFault => ExcuseDto.NoFault,
        Excuse.UnderFourteen => ExcuseDto.UnderFourteen,
        Excuse.Incapable => ExcuseDto.Incapable,
        Excuse.PursuantToLaw => ExcuseDto.PursuantToLaw,
        Excuse.SuperiorOrder => ExcuseDto.SuperiorOrder,
        Excuse.SelfDefense => ExcuseDto.SelfDefense,
        Excuse.Necessity => ExcuseDto.Necessity,
        Excuse.CriminalPriority => ExcuseDto.CriminalPriority,
        Excuse.UnavoidableIgnorance
            or Excuse.Unexpectable
            or Excuse.PettyOffense
            or Excuse.WithinCurePeriod => null,
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