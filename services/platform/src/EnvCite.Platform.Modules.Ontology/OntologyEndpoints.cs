using EnvCite.Ontology.V2;
using EnvCite.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Ontology;

/// <param name="Identifier">複合自然鍵識別碼（中央與地方雙軌）。</param>
/// <param name="Uri">由法規組成，可反查到該法規。</param>
/// <param name="DateDocument">制定（首次公布）日。</param>
public sealed record LawDto(
    LawIdentifierDto Identifier,
    string Uri,
    string Title,
    string Language,
    string? DateDocument,
    string? ApprovalAuthority = null,
    string? ApprovalDocNo = null,
    string? RatifiedAt = null,
    string? InvalidatedBy = null,
    string? InvalidationDate = null);

/// <param name="Uri">由法規與條號組成，可反查到該條的現行版本。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
public sealed record ArticleDto(string Uri, string Number);

/// <param name="Query">要查的原始文字；服務端以建索引時的同一個分詞器分詞。</param>
/// <param name="AsOf">查詢哪一天有效的條文（<c>YYYY-MM-DD</c>）；省略表示現行。</param>
/// <param name="Limit">最多幾筆，1–50；省略為 10。</param>
public sealed record SearchRequest(string? Query, string? AsOf, int? Limit);

/// <param name="Uri">由法規與條號組成，可反查到該條。</param>
/// <param name="Identifier">所屬法規的複合自然鍵識別碼。</param>
/// <param name="LawTitle">查詢日有效版本的法規標題。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
/// <param name="Score">字面相關度；只在同一次回應內可比，跨查詢不可比。</param>
public sealed record SearchHitDto(string Uri, LawIdentifierDto Identifier, string LawTitle, string Number, string Content, float Score);

public static class OntologyEndpoints
{
    public const string GetLawOperation = "ontology.get_law";
    public const string ListArticlesOperation = "ontology.list_articles";
    public const string SearchOperation = "ontology.search";

    public static LawIdentifier ParseLawIdentifier(string lawId)
    {
        var parts = lawId.Split(':', 3);
        return parts.Length switch
        {
            3 => new LawIdentifier { Jurisdiction = parts[0], IdScheme = parts[1], LocalId = parts[2] },
            2 => new LawIdentifier { Jurisdiction = parts[0], IdScheme = "local_glrs", LocalId = parts[1] },
            _ => new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = lawId },
        };
    }

    /// <summary>包在內部 gRPC 服務外的對外 REST facade；成功的呼叫會被計量。</summary>
    public static RouteGroupBuilder MapOntologyEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/laws/{lawId}", async (string lawId, string? asOf, OntologyService.OntologyServiceClient client,
            IOptions<OntologyOptions> options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var identifier = ParseLawIdentifier(lawId);
                var response = await client.GetLawAsync(new GetLawRequest { Identifier = identifier, AsOf = asOf ?? string.Empty },
                    deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: ct);
                await usage.RecordAsync(GetLawOperation, 1, ct);
                var law = response.Law;
                var lawIdDto = new LawIdentifierDto(law.Identifier?.Jurisdiction ?? "TW-CENTRAL", law.Identifier?.IdScheme ?? "moj_pcode", law.Identifier?.LocalId ?? lawId);
                return Results.Ok(new LawDto(
                    lawIdDto,
                    law.Iri,
                    law.Title,
                    law.Language,
                    string.IsNullOrEmpty(law.DateDocument) ? null : law.DateDocument,
                    string.IsNullOrEmpty(law.ApprovalAuthority) ? null : law.ApprovalAuthority,
                    string.IsNullOrEmpty(law.ApprovalDocNo) ? null : law.ApprovalDocNo,
                    string.IsNullOrEmpty(law.RatifiedAt) ? null : law.RatifiedAt,
                    string.IsNullOrEmpty(law.InvalidatedBy) ? null : law.InvalidatedBy,
                    string.IsNullOrEmpty(law.InvalidationDate) ? null : law.InvalidationDate));
            }))
            .WithName("GetLaw")
            .WithTags("法規")
            .WithSummary("取得法規")
            .WithDescription("`lawId` 為法規識別碼（支援自然鍵複合識別碼或全國法規資料庫 pcode）；標題取自 `asOf` 當日有效的版本，省略 `asOf`（`YYYY-MM-DD`）表示現行。"
                + "該日沒有有效的版本回 404，不會退回現行版本。成功計量為一次 `ontology.get_law`。")
            .Produces<LawDto>()
            .ProducesValidationProblem()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        v1.MapGet("/laws/{lawId}/articles", async (string lawId, string? asOf, OntologyService.OntologyServiceClient client,
            IOptions<OntologyOptions> options, IUsageRecorder usage, CancellationToken ct) =>
            await CallAsync(async () =>
            {
                var identifier = ParseLawIdentifier(lawId);
                var response = await client.ListArticlesAsync(new ListArticlesRequest { Identifier = identifier, AsOf = asOf ?? string.Empty },
                    deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: ct);
                await usage.RecordAsync(ListArticlesOperation, 1, ct);
                return Results.Ok(response.Articles.Select(a => new ArticleDto(a.Iri, a.Number)));
            }))
            .WithName("ListArticles")
            .WithTags("法規")
            .WithSummary("列出法規某日有效的各條")
            .WithDescription("依條次排序；`number` 照來源寫法（如 `5-1`）。省略 `asOf`（`YYYY-MM-DD`）表示現行；該日沒有有效的版本回 404，不會退回現行版本。"
                + "成功計量為一次 `ontology.list_articles`。")
            .Produces<IEnumerable<ArticleDto>>()
            .ProducesValidationProblem()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        // POST 而不是 GET：查詢全文是客戶案件的內容，放在網址裡會被請求日誌與追蹤記下來。
        v1.MapPost("/search", async (SearchRequest request, ILawSearch search, CancellationToken ct) =>
            {
                var result = await search.SearchAsync(request.Query, request.AsOf, request.Limit, ct);
                return result.Status switch
                {
                    AdminStatus.Ok => Results.Ok(result.Value!.Select(h =>
                        new SearchHitDto(h.Uri, h.Identifier, h.LawTitle, h.Number, h.Content, h.Score))),
                    AdminStatus.Invalid => result.ValidationProblem(),
                    _ => result.Problem(StatusCodes.Status503ServiceUnavailable, "Ontology service unavailable."),
                };
            })
            .WithName("SearchArticles")
            .WithTags("法規")
            .WithSummary("搜尋條文")
            .WithDescription(
                "以字面檢索查詢某日有效的條文，相關度高的在前。`query` 必填，為原始文字（服務端自行分詞）；"
                + "`asOf` 為 ISO 8601 日期（`YYYY-MM-DD`），省略表示現行；`limit` 為 1–50，省略為 10。"
                + "用 POST 是為了讓查詢文字不出現在網址裡；查詢文字不寫入日誌。成功計量為一次 `ontology.search`（0 筆命中也計一次）。")
            .Produces<IEnumerable<SearchHitDto>>()
            .ProducesValidationProblem()
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
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [ex.Status.Detail] });
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return Results.Problem("Ontology service unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
        }
    }
}