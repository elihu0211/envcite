using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Ontology;

/// <param name="Id">全國法規資料庫的 pcode（如 <c>O0020001</c>）。</param>
/// <param name="Uri">依 ELI 網址樣板，可反查到該法規。</param>
/// <param name="DateDocument">制定（首次公布）日；ELI 的 <c>date_document</c>。</param>
public sealed record LawDto(string Id, string Uri, string Title, string Language, string? DateDocument);

/// <param name="Uri">依 ELI 網址樣板，可反查到該條的現行版本。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
public sealed record ArticleDto(string Uri, string Number);

public static class OntologyEndpoints
{
    public const string GetLawOperation = "ontology.get_law";
    public const string ListArticlesOperation = "ontology.list_articles";

    /// <summary>包在內部 gRPC 服務外的對外 REST facade；成功的呼叫會被計量。</summary>
    public static RouteGroupBuilder MapOntologyEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/laws/{lawId}", async (string lawId, OntologyService.OntologyServiceClient client,
            IOptions<OntologyOptions> options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var response = await client.GetLawAsync(new GetLawRequest { Id = lawId },
                    deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: ct);
                await usage.RecordAsync(GetLawOperation, 1, ct);
                var law = response.Law;
                return Results.Ok(new LawDto(law.Id, law.Iri, law.Title, law.Language,
                    string.IsNullOrEmpty(law.DateDocument) ? null : law.DateDocument));
            }))
            .WithName("GetLaw")
            .WithTags("法規")
            .WithSummary("取得法規")
            .WithDescription("`lawId` 為全國法規資料庫的 pcode（如 `O0020001`）；標題取自現行版本。成功計量為一次 `ontology.get_law`。")
            .Produces<LawDto>()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        v1.MapGet("/laws/{lawId}/articles", async (string lawId, OntologyService.OntologyServiceClient client,
            IOptions<OntologyOptions> options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var response = await client.ListArticlesAsync(new ListArticlesRequest { LawId = lawId },
                    deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: ct);
                await usage.RecordAsync(ListArticlesOperation, 1, ct);
                return Results.Ok(response.Articles.Select(a => new ArticleDto(a.Iri, a.Number)));
            }))
            .WithName("ListArticles")
            .WithTags("法規")
            .WithSummary("列出法規現行有效的各條")
            .WithDescription("依條次排序；`number` 照來源寫法（如 `5-1`）。成功計量為一次 `ontology.list_articles`。")
            .Produces<IEnumerable<ArticleDto>>()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

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