using System.Security.Claims;

using EnvCite.Platform.Data;

namespace EnvCite.Platform.Modules.Ontology;

internal sealed class LawCorpusService(TenantScopes tenants, ITenantModules modules) : ILawCorpusService
{
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

            var articles = await gateway.ListArticlesAsync(jurisdiction, idScheme, localId, asOf, cancellationToken);
            if (!articles.IsOk)
                return AdminOutcome.Unavailable<PortalLawCorpusResult>(articles.Code ?? OutcomeCodes.OntologyUnavailable);

            var dto = law.Value!;
            return AdminOutcome.Ok(new PortalLawCorpusResult(
                dto.Title,
                dto.Language,
                dto.Uri,
                dto.DateDocument,
                [.. articles.Value!.Select(a => new PortalLawArticle(a.Uri, a.Number))]));
        });
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
            var penalty = new AdministrativePenaltyFactsDto(
                Intent: null, Negligence: null, AgentFault: null, Age: null, Incapable: null, SelfInduced: null,
                PursuantToLaw: null, SuperiorOrder: null, KnewIllegal: null, StatedOpinion: null, SelfDefense: null,
                DefenseExcessive: null, Necessity: null, NecessityExcessive: null, CapacityReduced: null, IgnoranceOfLaw: null,
                CriminalOffense: input.CriminalOffense,
                CourtConfiscated: input.CourtConfiscated,
                CriminalPayment: string.IsNullOrWhiteSpace(input.CriminalPayment) ? null : input.CriminalPayment.Trim(),
                GainedBenefit: string.IsNullOrWhiteSpace(input.GainedBenefit) ? null : input.GainedBenefit.Trim());

            var request = new EvaluateReasoningRequest(
                ConductOn: string.IsNullOrWhiteSpace(input.ConductOn) ? null : input.ConductOn.Trim(),
                DecidedOn: string.IsNullOrWhiteSpace(input.DecidedOn) ? null : input.DecidedOn.Trim(),
                Facts: [],
                AdministrativePenalty: penalty,
                ConductEndedOn: string.IsNullOrWhiteSpace(input.ConductEndedOn) ? null : input.ConductEndedOn.Trim(),
                ResultOn: null);

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

            return AdminOutcome.Ok(new PortalReasoningOutcome(
                verdicts,
                dto.Earlier.Count,
                atDecision.Invalidated.Count,
                atDecision.DelegationBases.Count(d => !d.Rooted),
                atDecision.UncoveredLawIdentifiers.Count));
        });
    }

    private static string? PortalTenant(ClaimsPrincipal user) =>
        user.Identity?.IsAuthenticated == true && user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { Length: > 0 } tenant ? tenant : null
            : null;
}