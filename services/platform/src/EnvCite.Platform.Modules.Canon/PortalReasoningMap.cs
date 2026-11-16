using System.Globalization;

namespace EnvCite.Platform.Modules.Canon;

/// <summary>
/// 入口推論表單與公開推論契約之間的對映。表單只帶入口收集得到的欄位，其餘維持省略（省略是未知，不是否）。
/// 結果只留下入口要顯示的計數、候選與缺漏，不重算。
/// </summary>
internal static class PortalReasoningMap
{
    public static EvaluateReasoningRequest Request(PortalReasoningInput input)
    {
        var facts = new List<FactDto>();
        if (!string.IsNullOrWhiteSpace(input.PollutantCode) && !string.IsNullOrWhiteSpace(input.PollutantValue))
        {
            facts.Add(new FactDto(
                input.PollutantCode.Trim(),
                [input.PollutantValue.Trim()],
                null,
                Unit: string.IsNullOrWhiteSpace(input.PollutantUnit) ? null : input.PollutantUnit.Trim()));
        }

        if (!string.IsNullOrWhiteSpace(input.IndustryClass))
            facts.Add(new FactDto("industry_class", null, null, [input.IndustryClass.Trim()]));

        var penalty = new AdministrativePenaltyFactsDto(
            Intent: input.Intent, Negligence: input.Negligence, AgentFault: null, Age: null,
            Incapable: null, SelfInduced: input.SelfInduced,
            PursuantToLaw: null, SuperiorOrder: null, KnewIllegal: null, StatedOpinion: null,
            SelfDefense: input.SelfDefense, DefenseExcessive: input.DefenseExcessive,
            Necessity: input.Necessity, NecessityExcessive: input.NecessityExcessive,
            CapacityReduced: input.CapacityReduced, IgnoranceOfLaw: null,
            CriminalOffense: input.CriminalOffense,
            CourtConfiscated: input.CourtConfiscated,
            CriminalPayment: string.IsNullOrWhiteSpace(input.CriminalPayment) ? null : input.CriminalPayment.Trim(),
            GainedBenefit: string.IsNullOrWhiteSpace(input.GainedBenefit) ? null : input.GainedBenefit.Trim(),
            FactMistake: input.FactMistake,
            IgnoranceUnavoidable: input.IgnoranceUnavoidable,
            PettyMinor: input.PettyMinor);

        PerOccasionTimelineDto? timeline = null;
        if (!string.IsNullOrWhiteSpace(input.CureDeadline) || !string.IsNullOrWhiteSpace(input.ReinspectionOn))
        {
            timeline = new PerOccasionTimelineDto(
                null,
                string.IsNullOrWhiteSpace(input.CureDeadline) ? null : input.CureDeadline.Trim(),
                null,
                string.IsNullOrWhiteSpace(input.ReinspectionOn) ? null : input.ReinspectionOn.Trim());
        }

        var scoringInputs = new List<ScoringInputDto>();
        if (!string.IsNullOrWhiteSpace(input.ScoringMatrixCode))
        {
            var factors = new List<ScoringFactorValueDto>();
            if (!string.IsNullOrWhiteSpace(input.DemeritPoints))
                factors.Add(new ScoringFactorValueDto("demerit_points", input.DemeritPoints.Trim()));
            scoringInputs.Add(new ScoringInputDto(input.ScoringMatrixCode.Trim(), factors));
        }

        return new EvaluateReasoningRequest(
            ConductOn: string.IsNullOrWhiteSpace(input.ConductOn) ? null : input.ConductOn.Trim(),
            DecidedOn: string.IsNullOrWhiteSpace(input.DecidedOn) ? null : input.DecidedOn.Trim(),
            Facts: facts,
            AdministrativePenalty: penalty,
            ConductEndedOn: string.IsNullOrWhiteSpace(input.ConductEndedOn) ? null : input.ConductEndedOn.Trim(),
            ResultOn: null,
            Timeline: timeline,
            ScoringInputs: scoringInputs.Count > 0 ? scoringInputs : null);
    }

    public static PortalReasoningOutcome Outcome(ReasoningResultDto dto)
    {
        var atDecision = dto.AtDecision;
        var sources = atDecision.Sources.ToDictionary(s => s.StatementId, s => s.Uri);
        var missingCounts = atDecision.Missing
            .GroupBy(m => m.StatementId)
            .ToDictionary(g => g.Key, g => g.Count());
        var verdicts = atDecision.Verdicts
            .Select(v => new PortalReasoningVerdict(
                sources.TryGetValue(v.StatementId, out var uri) ? uri : "-",
                v.StatementId.ToString(CultureInfo.InvariantCulture),
                v.Verdict.ToString(),
                missingCounts.GetValueOrDefault(v.StatementId)))
            .ToList();

        var penalties = atDecision.Penalties
            .Select(p => new PortalPenaltyItem(
                p.StatementId.ToString(CultureInfo.InvariantCulture),
                p.Effect.ToString(),
                p.MinAmount,
                p.MaxAmount,
                p.Conditional,
                p.PerOccasion,
                p.Discretionary))
            .ToList();

        var defenses = new List<PortalTierDefense>();
        if (atDecision.General.Justified is { } justified)
        {
            defenses.AddRange(justified.Select(j => new PortalTierDefense(
                "Tier2_Unlawfulness",
                j.PenaltyStatementId.ToString(CultureInfo.InvariantCulture),
                j.Excuse.ToString())));
        }

        if (atDecision.General.Excused is { } excused)
        {
            defenses.AddRange(excused.Select(e => new PortalTierDefense(
                "Tier3_Culpability",
                e.PenaltyStatementId.ToString(CultureInfo.InvariantCulture),
                e.Excuse.ToString())));
        }

        defenses.AddRange(atDecision.General.Mitigation.Select(m => new PortalTierDefense(
            "Mitigation",
            m.PenaltyStatementId.ToString(CultureInfo.InvariantCulture),
            m.Mitigation.ToString())));

        var scoringCards = (atDecision.ScoringResults ?? [])
            .Select(s => new PortalScoringCard(
                s.MatrixCode,
                s.LawTitle,
                s.FormulaKind,
                s.EquationSummary,
                s.ClampedMin,
                s.ClampedMax,
                s.LectureHoursMin,
                s.LectureHoursMax,
                s.DemeritTriggered,
                s.DemeritTotal,
                s.MissingFactors,
                s.JudgementFactors))
            .ToList();

        return new PortalReasoningOutcome(
            verdicts,
            dto.Earlier.Count,
            atDecision.Invalidated.Count,
            atDecision.DelegationBases.Count(d => !d.Rooted),
            atDecision.UncoveredLawIdentifiers.Count,
            penalties,
            defenses,
            scoringCards,
            [.. atDecision.Missing.Select(m => m.Concept).Distinct()],
            [.. atDecision.NeedsAssessment.Select(n => n.Concept).Distinct()],
            [.. atDecision.Problems.Select(p => $"{p.Kind}: {p.Concept ?? p.ConditionId?.ToString(CultureInfo.InvariantCulture) ?? "-"}")],
            atDecision.OneAct?.MinAmount,
            atDecision.OneAct?.MaxAmount);
    }
}