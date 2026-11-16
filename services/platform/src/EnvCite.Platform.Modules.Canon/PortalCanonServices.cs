using System.Security.Claims;

using EnvCite.Platform.Data;

using Microsoft.Extensions.Caching.Memory;

namespace EnvCite.Platform.Modules.Canon;

internal sealed class LawCorpusService(
    TenantScopes tenants,
    ITenantModules modules,
    IMemoryCache cache) : ILawCorpusService
{
    private static readonly TimeSpan ReadCacheTtl = TimeSpan.FromSeconds(30);

    public Task<Outcome<PortalLawTreeResult>> TreeAsync(
        ClaimsPrincipal user, string? jurisdiction, string? asOf, CancellationToken cancellationToken) =>
        TreeByCategoryAsync(user, jurisdiction, null, asOf, cancellationToken);

    public async Task<Outcome<PortalLawTreeResult>> TreeByCategoryAsync(
        ClaimsPrincipal user, string? jurisdiction, string? categoryPrefix, string? asOf, CancellationToken cancellationToken)
    {
        var cacheKey = $"corpus:tree:{jurisdiction}:{categoryPrefix}:{asOf}";
        return await ReadAsync(user, async scope =>
        {
            var gateway = scope.Get<CanonGateway>();
            var outcome = await gateway.ListLawsAsync(jurisdiction, asOf, cancellationToken, categoryPrefix);
            if (!outcome.IsOk)
                return outcome.ForwardError<PortalLawTreeResult>();

            var (laws, available) = outcome.Value;
            return Outcomes.Ok(new PortalLawTreeResult(LawCatalog.Tree(laws), available));
        }, cacheKey, r => r.CatalogAvailable);
    }

    public async Task<Outcome<PortalLawGraphResult>> GraphAsync(
        ClaimsPrincipal user, PortalLawGraphQuery query, CancellationToken cancellationToken)
    {
        var focus = query.Focus;
        var cacheKey = $"corpus:graph:{focus?.Jurisdiction}:{focus?.IdScheme}:{focus?.LocalId}:{query.AsOf}:{query.CategoryPrefix}:{query.UpstreamDepth}:{query.DownstreamDepth}:{query.ChildLimit}:{query.ChildDocumentType}";
        return await ReadAsync(user, scope =>
            scope.Get<CanonGateway>().GetLawGraphAsync(query, cancellationToken),
            cacheKey, r => r.Available);
    }

    public Task<Outcome<PortalLawCorpusResult>> BrowseAsync(
        ClaimsPrincipal user, string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken) =>
        ReadAsync(user, async scope =>
        {
            var gateway = scope.Get<CanonGateway>();
            var law = await gateway.GetLawAsync(jurisdiction, idScheme, localId, asOf, cancellationToken);
            if (!law.IsOk)
                return Outcomes.NotFound<PortalLawCorpusResult>(law.Code ?? OutcomeCodes.LawNotFound);

            var structure = await gateway.ListLawStructureAsync(jurisdiction, idScheme, localId, asOf, cancellationToken);
            if (!structure.IsOk)
                return Outcomes.Unavailable<PortalLawCorpusResult>(structure.Code ?? OutcomeCodes.CanonUnavailable);

            var dto = law.Value!;
            var data = structure.Value!;
            return Outcomes.Ok(new PortalLawCorpusResult(
                dto.Title,
                dto.Language,
                dto.Uri,
                dto.DateDocument,
                [.. data.Articles.Select(a => new PortalLawArticle(a.Uri, a.Number))],
                LawCatalog.Structure(data.Headings, data.Articles)));
        });

    private async Task<Outcome<T>> ReadAsync<T>(
        ClaimsPrincipal user,
        Func<TenantScope, Task<Outcome<T>>> action,
        string? cacheKey = null,
        Func<T, bool>? shouldCache = null)
    {
        if (PortalCaller.Tenant(user) is not { } identifier || !modules.IsEnabled(identifier, ModuleKeys.LawCorpus))
            return Outcomes.Forbidden<T>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        if (cacheKey is not null && cache.TryGetValue(cacheKey, out Outcome<T>? cached) && cached is not null)
            return cached;

        var result = await tenants.InByIdentifierAsync(identifier, action);
        if (cacheKey is not null && shouldCache is not null && result.IsOk && result.Value is { } value && shouldCache(value))
            cache.Set(cacheKey, result, ReadCacheTtl);
        return result;
    }
}

internal sealed class NormativeReasoningService(TenantScopes tenants, ITenantModules modules) : INormativeReasoningService
{
    public async Task<Outcome<PortalReasoningOutcome>> EvaluateAsync(
        ClaimsPrincipal user, PortalReasoningInput input, CancellationToken cancellationToken)
    {
        if (PortalCaller.Tenant(user) is not { } identifier || !modules.IsEnabled(identifier, ModuleKeys.NormativeReasoning))
            return Outcomes.Forbidden<PortalReasoningOutcome>(OutcomeCodes.ModuleDisabled, ModuleKeys.NormativeReasoning);

        return await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var outcome = await scope.Get<CanonGateway>().EvaluateAsync(PortalReasoningMap.Request(input), cancellationToken);
            return outcome.IsOk
                ? Outcomes.Ok(PortalReasoningMap.Outcome(outcome.Value!))
                : outcome.ForwardError<PortalReasoningOutcome>();
        });
    }
}

internal sealed class CitationArticleService(TenantScopes tenants) : ICitationArticleService
{
    public async Task<Outcome<PortalArticleDetail>> GetByIriAsync(
        ClaimsPrincipal user, string? iri, CancellationToken cancellationToken)
    {
        if (PortalCaller.Tenant(user) is not { } identifier)
            return Outcomes.Forbidden<PortalArticleDetail>(OutcomeCodes.ModuleDisabled, ModuleKeys.LawCorpus);

        return await tenants.InByIdentifierAsync(identifier, async scope =>
        {
            var gateway = scope.Get<CanonGateway>();
            var outcome = await gateway.GetArticleByIriAsync(iri, cancellationToken);
            if (!outcome.IsOk || outcome.Value is not { } dto)
                return outcome.ForwardError<PortalArticleDetail>();

            return Outcomes.Ok(new PortalArticleDetail(
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