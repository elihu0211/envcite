using System.Security.Claims;

using EnvCite.Platform.Data;

using Microsoft.Extensions.Caching.Memory;

namespace EnvCite.Platform.Modules.Ontology;

internal sealed class LawCorpusService(
    TenantScopes tenants,
    ITenantModules modules,
    IMemoryCache cache) : ILawCorpusService
{
    private static readonly TimeSpan ReadCacheTtl = TimeSpan.FromSeconds(30);

    public Task<AdminOutcome<PortalLawTreeResult>> TreeAsync(
        ClaimsPrincipal user, string? jurisdiction, string? asOf, CancellationToken cancellationToken) =>
        TreeByCategoryAsync(user, jurisdiction, null, asOf, cancellationToken);

    public async Task<AdminOutcome<PortalLawTreeResult>> TreeByCategoryAsync(
        ClaimsPrincipal user, string? jurisdiction, string? categoryPrefix, string? asOf, CancellationToken cancellationToken)
    {
        if (PortalTenant(user) is not { } identifier)
            return AdminOutcome.Forbidden<PortalLawTreeResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        if (!modules.IsEnabled(identifier, ModuleKeys.LawCorpus))
            return AdminOutcome.Forbidden<PortalLawTreeResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        var cacheKey = $"corpus:tree:{jurisdiction}:{categoryPrefix}:{asOf}";
        if (cache.TryGetValue(cacheKey, out AdminOutcome<PortalLawTreeResult>? cached) && cached is not null)
            return cached;

        var result = await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<OntologyGateway>();
            var outcome = await gateway.ListLawsAsync(jurisdiction, asOf, cancellationToken, categoryPrefix);
            if (!outcome.IsOk)
                return new AdminOutcome<PortalLawTreeResult>(outcome.Status, default, outcome.Message, outcome.Code, outcome.Args);

            var (laws, available) = outcome.Value;
            return AdminOutcome.Ok(new PortalLawTreeResult(BuildTree(laws), available));
        });
        if (result.IsOk && result.Value is { CatalogAvailable: true })
            cache.Set(cacheKey, result, ReadCacheTtl);
        return result;
    }

    public async Task<AdminOutcome<PortalLawGraphResult>> GraphAsync(
        ClaimsPrincipal user, PortalLawGraphQuery query, CancellationToken cancellationToken)
    {
        if (PortalTenant(user) is not { } identifier)
            return AdminOutcome.Forbidden<PortalLawGraphResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        if (!modules.IsEnabled(identifier, ModuleKeys.LawCorpus))
            return AdminOutcome.Forbidden<PortalLawGraphResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        var focus = query.Focus;
        var cacheKey = $"corpus:graph:{focus?.Jurisdiction}:{focus?.IdScheme}:{focus?.LocalId}:{query.AsOf}:{query.CategoryPrefix}:{query.UpstreamDepth}:{query.DownstreamDepth}:{query.ChildLimit}:{query.ChildDocumentType}";
        if (cache.TryGetValue(cacheKey, out AdminOutcome<PortalLawGraphResult>? cached) && cached is not null)
            return cached;

        var result = await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<OntologyGateway>();
            return await gateway.GetLawGraphAsync(query, cancellationToken);
        });
        if (result.IsOk && result.Value is { Available: true })
            cache.Set(cacheKey, result, ReadCacheTtl);
        return result;
    }

    public async Task<AdminOutcome<PortalLawCorpusResult>> BrowseAsync(
        ClaimsPrincipal user, string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken)
    {
        if (PortalTenant(user) is not { } identifier)
            return AdminOutcome.Forbidden<PortalLawCorpusResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        if (!modules.IsEnabled(identifier, ModuleKeys.LawCorpus))
            return AdminOutcome.Forbidden<PortalLawCorpusResult>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        return await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<OntologyGateway>();
            var law = await gateway.GetLawAsync(jurisdiction, idScheme, localId, asOf, cancellationToken);
            if (!law.IsOk)
                return AdminOutcome.NotFound<PortalLawCorpusResult>(law.Code ?? OutcomeCodes.LawNotFound);

            var structure = await gateway.ListLawStructureAsync(jurisdiction, idScheme, localId, asOf, cancellationToken);
            if (!structure.IsOk)
                return AdminOutcome.Unavailable<PortalLawCorpusResult>(structure.Code ?? OutcomeCodes.OntologyUnavailable);

            var dto = law.Value!;
            var data = structure.Value!;
            return AdminOutcome.Ok(new PortalLawCorpusResult(
                dto.Title,
                dto.Language,
                dto.Uri,
                dto.DateDocument,
                [.. data.Articles.Select(a => new PortalLawArticle(a.Uri, a.Number))],
                BuildStructure(data.Headings, data.Articles)));
        });
    }

    private static IReadOnlyList<PortalStructureNode> BuildStructure(
        IReadOnlyList<StructureHeadingDto> headings,
        IReadOnlyList<ArticleWithSortDto> articles)
    {
        if (headings.Count == 0)
            return [];

        var childHeadingIndices = Enumerable.Range(0, headings.Count).Select(_ => new List<int>()).ToArray();
        var rootIndices = new List<int>();
        for (var i = 0; i < headings.Count; i++)
        {
            var parent = headings[i].ParentIndex;
            if (parent >= 0 && parent < headings.Count && parent != i)
                childHeadingIndices[parent].Add(i);
            else
                rootIndices.Add(i);
        }

        var ownedArticles = Enumerable.Range(0, headings.Count).Select(_ => new List<PortalLawArticle>()).ToArray();
        var hIdx = 0;
        foreach (var article in articles)
        {
            while (hIdx + 1 < headings.Count && CompareSortKeys(headings[hIdx + 1].SortKey, article.SortKey) <= 0)
                hIdx++;
            if (CompareSortKeys(headings[hIdx].SortKey, article.SortKey) <= 0)
                ownedArticles[hIdx].Add(new PortalLawArticle(article.Uri, article.Number));
        }

        var visited = new HashSet<int>();
        PortalStructureNode ToNode(int index)
        {
            var h = headings[index];
            if (!visited.Add(index))
                return new PortalStructureNode(h.Kind, h.HeadingNo, h.Title, [], []);
            var children = childHeadingIndices[index].Select(ToNode).ToList();
            return new PortalStructureNode(h.Kind, h.HeadingNo, h.Title, children, ownedArticles[index]);
        }

        return [.. rootIndices.Select(ToNode)];
    }

    private static int CompareSortKeys(IReadOnlyList<int> left, IReadOnlyList<int> right)
    {
        var min = Math.Min(left.Count, right.Count);
        for (var i = 0; i < min; i++)
        {
            var cmp = left[i].CompareTo(right[i]);
            if (cmp != 0)
                return cmp;
        }
        return left.Count.CompareTo(right.Count);
    }

    private static IReadOnlyList<PortalLawTreeNode> BuildTree(IReadOnlyList<LawSummaryDto> laws)
    {
        static string KeyOf(LawIdentifierDto id) => $"{id.Jurisdiction}:{id.IdScheme}:{id.LocalId}";

        var childrenByParent = new Dictionary<string, List<LawSummaryDto>>(StringComparer.Ordinal);
        var roots = new List<LawSummaryDto>();
        var knownKeys = new HashSet<string>(laws.Select(l => KeyOf(l.Identifier)), StringComparer.Ordinal);

        foreach (var law in laws)
        {
            if (law.ParentIdentifier is { } parent && knownKeys.Contains(KeyOf(parent)))
            {
                var pKey = KeyOf(parent);
                if (!childrenByParent.TryGetValue(pKey, out var bucket))
                {
                    bucket = [];
                    childrenByParent[pKey] = bucket;
                }
                bucket.Add(law);
            }
            else
            {
                roots.Add(law);
            }
        }

        PortalLawTreeNode ToNode(LawSummaryDto item)
        {
            var kids = childrenByParent.TryGetValue(KeyOf(item.Identifier), out var list)
                ? list.Select(ToNode).ToList()
                : [];
            return new PortalLawTreeNode(
                item.Identifier.Jurisdiction,
                item.Identifier.IdScheme,
                item.Identifier.LocalId,
                item.Title,
                item.NormKind,
                item.DateDocument,
                kids,
                item.DocumentType,
                item.ChildCount,
                item.ParentCount,
                item.GuidanceCount);
        }

        return [.. roots.Select(ToNode)];
    }

    private static string? PortalTenant(ClaimsPrincipal user) =>
        user.Identity?.IsAuthenticated == true && user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { Length: > 0 } tenant ? tenant : null
            : null;
}

internal sealed class NormativeReasoningService(TenantScopes tenants, ITenantModules modules) : INormativeReasoningService
{
    public async Task<AdminOutcome<PortalReasoningOutcome>> EvaluateAsync(
        ClaimsPrincipal user, PortalReasoningInput input, CancellationToken cancellationToken)
    {
        if (PortalTenant(user) is not { } identifier)
            return AdminOutcome.Forbidden<PortalReasoningOutcome>(OutcomeCodes.ModuleDisabled, ModuleKeys.NormativeReasoning);

        if (!modules.IsEnabled(identifier, ModuleKeys.NormativeReasoning))
            return AdminOutcome.Forbidden<PortalReasoningOutcome>(OutcomeCodes.ModuleDisabled, ModuleKeys.NormativeReasoning);

        return await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<OntologyGateway>();
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
            {
                facts.Add(new FactDto("industry_class", null, null, [input.IndustryClass.Trim()]));
            }

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
                {
                    factors.Add(new ScoringFactorValueDto("demerit_points", input.DemeritPoints.Trim()));
                }
                scoringInputs.Add(new ScoringInputDto(input.ScoringMatrixCode.Trim(), factors));
            }

            var request = new EvaluateReasoningRequest(
                ConductOn: string.IsNullOrWhiteSpace(input.ConductOn) ? null : input.ConductOn.Trim(),
                DecidedOn: string.IsNullOrWhiteSpace(input.DecidedOn) ? null : input.DecidedOn.Trim(),
                Facts: facts,
                AdministrativePenalty: penalty,
                ConductEndedOn: string.IsNullOrWhiteSpace(input.ConductEndedOn) ? null : input.ConductEndedOn.Trim(),
                ResultOn: null,
                Timeline: timeline,
                ScoringInputs: scoringInputs.Count > 0 ? scoringInputs : null);

            var outcome = await gateway.EvaluateAsync(request, cancellationToken);
            if (!outcome.IsOk)
                return new AdminOutcome<PortalReasoningOutcome>(outcome.Status, default, outcome.Message, outcome.Code, outcome.Args);

            var dto = outcome.Value!;
            var atDecision = dto.AtDecision;
            var sources = atDecision.Sources.ToDictionary(s => s.StatementId, s => s.Uri);
            var missingCounts = atDecision.Missing
                .GroupBy(m => m.StatementId)
                .ToDictionary(g => g.Key, g => g.Count());
            var verdicts = atDecision.Verdicts
                .Select(v => new PortalReasoningVerdict(
                    sources.TryGetValue(v.StatementId, out var uri) ? uri : "-",
                    v.StatementId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    v.Verdict.ToString(),
                    missingCounts.GetValueOrDefault(v.StatementId)))
                .ToList();

            var penalties = atDecision.Penalties
                .Select(p => new PortalPenaltyItem(
                    p.StatementId.ToString(System.Globalization.CultureInfo.InvariantCulture),
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
                    j.PenaltyStatementId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    j.Excuse.ToString())));
            }
            if (atDecision.General.Excused is { } excused)
            {
                defenses.AddRange(excused.Select(e => new PortalTierDefense(
                    "Tier3_Culpability",
                    e.PenaltyStatementId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    e.Excuse.ToString())));
            }
            defenses.AddRange(atDecision.General.Mitigation.Select(m => new PortalTierDefense(
                "Mitigation",
                m.PenaltyStatementId.ToString(System.Globalization.CultureInfo.InvariantCulture),
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

            return AdminOutcome.Ok(new PortalReasoningOutcome(
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
                [.. atDecision.Problems.Select(p => $"{p.Kind}: {p.Concept ?? p.ConditionId?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "-"}")],
                atDecision.OneAct?.MinAmount,
                atDecision.OneAct?.MaxAmount));
        });
    }

    private static string? PortalTenant(ClaimsPrincipal user) =>
        user.Identity?.IsAuthenticated == true && user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { Length: > 0 } tenant ? tenant : null
            : null;
}

internal sealed class CitationArticleService(TenantScopes tenants) : ICitationArticleService
{
    public async Task<AdminOutcome<PortalArticleDetail>> GetByIriAsync(
        ClaimsPrincipal user, string? iri, CancellationToken cancellationToken)
    {
        if (user.Identity?.IsAuthenticated != true
            || user.FindFirst(PlatformClaimTypes.PortalUserId) is null
            || user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is not { Length: > 0 } identifier)
        {
            return AdminOutcome.Forbidden<PortalArticleDetail>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);
        }

        return await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<OntologyGateway>();
            var outcome = await gateway.GetArticleByIriAsync(iri, cancellationToken);
            if (!outcome.IsOk || outcome.Value is not { } dto)
                return new AdminOutcome<PortalArticleDetail>(outcome.Status, default, outcome.Message, outcome.Code, outcome.Args);

            return AdminOutcome.Ok(new PortalArticleDetail(
                dto.Uri,
                new PortalLawKey(dto.Identifier.Jurisdiction, dto.Identifier.IdScheme, dto.Identifier.LocalId),
                dto.LawTitle,
                dto.Number,
                dto.Content,
                dto.Language,
                dto.DateDocument,
                dto.NormKind,
                dto.DocumentType,
                dto.RepealedOn));
        });
    }
}