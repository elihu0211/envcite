using System.Text.Json.Serialization;

using EnvLaw.Consult.V2;
using EnvLaw.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvLaw.Platform.Modules.Consult;

/// <param name="Question">使用者的原句；不要先讓模型改寫。</param>
/// <param name="AsOf">ISO 8601 日期；省略表示現行條文。</param>
public sealed record GroundRequestDto(string Question, string? AsOf = null);

public sealed record CitationDto(string Code, string Iri, string Label, string Text);

public sealed record RefusalDto(string Reason, string Detail);

/// <summary>
/// <c>outcome</c> 為 <c>evidence</c> 時帶 <c>citations</c> 與 <c>confidence</c>；
/// 為 <c>refused</c> 時帶 <c>refusal</c>。拒答是結果，不是錯誤，故同為 200。
/// </summary>
public sealed record GroundResultDto(
    string Outcome,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] IReadOnlyList<CitationDto>? Citations = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? Confidence = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] RefusalDto? Refusal = null);

public static class ConsultEndpoints
{
    public const string GroundOperation = "consult.ground";

    /// <summary>
    /// 包在 env-law-consult 外的對外 REST facade。拒答也計量：它是完成的呼叫，不是錯誤；
    /// 只有錯誤（4xx／5xx）不計。
    /// </summary>
    public static RouteGroupBuilder MapConsultEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapPost("/ground", async (GroundRequestDto body, ConsultationService.ConsultationServiceClient client,
            ConsultOptions options, IUsageRecorder usage, CancellationToken ct) =>
        {
            GroundResponse response;
            try
            {
                response = await client.GroundAsync(
                    new GroundRequest { Question = body.Question ?? string.Empty, AsOf = body.AsOf ?? string.Empty },
                    deadline: DateTime.UtcNow.Add(options.Timeout), cancellationToken: ct);
            }
            catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
            {
                return Results.Problem(ex.Status.Detail, statusCode: StatusCodes.Status400BadRequest);
            }
            catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
            {
                return Results.Problem("Consult service unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
            }

            await usage.RecordAsync(GroundOperation, 1, ct);
            return Results.Ok(ToDto(response));
        })
            .WithName("Ground")
            .WithTags("接地")
            .WithSummary("為問句備妥可引用的證據，或拒答")
            .WithDescription("證據與拒答都回 200，以 `outcome` 區分；拒答是結果、不是錯誤，兩者都計量為一次 "
                + "`consult.ground`。證據的每一條引用有代號（`L1`、`L2`…），呼叫端的模型只能以代號引用。")
            .Produces<GroundResultDto>()
            .ProducesProblem(StatusCodes.Status400BadRequest)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        return v1;
    }

    private static GroundResultDto ToDto(GroundResponse response) => response.OutcomeCase switch
    {
        GroundResponse.OutcomeOneofCase.Evidence => new GroundResultDto(
            "evidence",
            [.. response.Evidence.Citations.Select(c => new CitationDto(c.Code, c.Iri, c.Label, c.Text))],
            response.Evidence.Confidence switch
            {
                Confidence.High => "high",
                Confidence.Low => "low",
                _ => throw new InvalidOperationException($"Unexpected confidence {response.Evidence.Confidence}."),
            },
            null),
        GroundResponse.OutcomeOneofCase.Refused => new GroundResultDto(
            "refused",
            null,
            null,
            new RefusalDto(
                response.Refused.Reason switch
                {
                    Refused.Types.Reason.NoEvidence => "no_evidence",
                    Refused.Types.Reason.OutOfScope => "out_of_scope",
                    Refused.Types.Reason.MissingInput => "missing_input",
                    _ => throw new InvalidOperationException($"Unexpected refusal reason {response.Refused.Reason}."),
                },
                response.Refused.Detail)),
        _ => throw new InvalidOperationException("Consult returned neither evidence nor a refusal."),
    };
}