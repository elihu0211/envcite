using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvLaw.Platform.Modules.Ontology;

public sealed record LawDto(string Id, string Iri, string Title, string Language, string? DateDocument);

public sealed record ArticleDto(string Iri, string Number);

public static class OntologyEndpoints
{
    public const string GetLawOperation = "ontology.get_law";
    public const string ListArticlesOperation = "ontology.list_articles";

    /// <summary>Public REST facade over the internal gRPC service; successful calls are metered.</summary>
    public static RouteGroupBuilder MapOntologyEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/laws/{lawId}", async (string lawId, OntologyService.OntologyServiceClient client,
            OntologyOptions options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var response = await client.GetLawAsync(new GetLawRequest { Id = lawId },
                    deadline: DateTime.UtcNow.Add(options.Timeout), cancellationToken: ct);
                await usage.RecordAsync(GetLawOperation, 1, ct);
                var law = response.Law;
                return Results.Ok(new LawDto(law.Id, law.Iri, law.Title, law.Language,
                    string.IsNullOrEmpty(law.DateDocument) ? null : law.DateDocument));
            }));

        v1.MapGet("/laws/{lawId}/articles", async (string lawId, OntologyService.OntologyServiceClient client,
            OntologyOptions options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var response = await client.ListArticlesAsync(new ListArticlesRequest { LawId = lawId },
                    deadline: DateTime.UtcNow.Add(options.Timeout), cancellationToken: ct);
                await usage.RecordAsync(ListArticlesOperation, 1, ct);
                return Results.Ok(response.Articles.Select(a => new ArticleDto(a.Iri, a.Number)));
            }));

        return v1;
    }

    private static async Task<IResult> CallAsync(Func<Task<IResult>> call)
    {
        try
        {
            return await call();
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.NotFound)
        {
            return Results.NotFound();
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return Results.Problem("Ontology service unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
        }
    }
}