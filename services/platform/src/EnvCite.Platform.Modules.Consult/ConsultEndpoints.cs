using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

using EnvCite.Consult.V2;
using EnvCite.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Consult;

/// <param name="Question">使用者的原句；不要先讓模型改寫。</param>
/// <param name="PointInTime">ISO 8601 日期：依哪一天施行的條文（時點版本）；省略表示現行條文。</param>
public sealed record GroundRequestDto(string Question, string? PointInTime = null);

/// <summary>一筆法源：代號、可反查到該條文版本的網址、正式引用格式與條文全文。</summary>
/// <param name="Code">單次回應內的代號（<c>L1</c>…）；呼叫端的模型只能以代號引用，不要儲存。</param>
/// <param name="Uri">由法規的 pcode、版本日與條號組成，可反查到該條文版本。</param>
/// <param name="Citation">正式引用格式，如「噪音管制法 第 9 條」。</param>
/// <param name="Text">該日施行的條文全文。</param>
public sealed record AuthorityDto(string Code, string Uri, string Citation, string Text);

public sealed record RefusalDto(string Reason, string Detail);

/// <summary>
/// <c>outcome</c> 為 <c>grounded</c>（有法源支撐）時帶 <c>authorities</c> 與 <c>basis</c>；
/// 為 <c>refused</c> 時帶 <c>refusal</c>。拒答是結果，不是錯誤，故同為 200。
/// <c>basis</c>：<c>direct</c> 表示至少一筆法源直接規範此問題，<c>indirect</c> 表示法源只是間接相關。
/// </summary>
public sealed record GroundResultDto(
    string Outcome,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] IReadOnlyList<AuthorityDto>? Authorities = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? Basis = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] RefusalDto? Refusal = null);

public static partial class ConsultEndpoints
{
    public const string GroundOperation = "consult.ground";

    public const string IdempotencyKeyHeader = "Idempotency-Key";

    public const string ReplayedHeader = "Idempotent-Replayed";

    /// <summary>
    /// 包在 envcite-consult 外的對外 REST facade。拒答也計量：它是完成的呼叫，不是錯誤；
    /// 只有錯誤（4xx／5xx）不計。
    /// </summary>
    public static RouteGroupBuilder MapConsultEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapPost("/ground", GroundAsync)
            .WithName("Ground")
            .WithTags("接地")
            .WithSummary("為問句備妥可引用的法源，或拒答")
            .WithDescription("有法源（`grounded`）與拒答（`refused`）都回 200，以 `outcome` 區分；拒答是結果、不是錯誤，"
                + "兩者都計量為一次 `consult.ground`。每一筆法源有代號（`L1`、`L2`…），呼叫端的模型只能以代號引用。\n\n"
                + "可帶 `Idempotency-Key`（1–255 個字元，限英數與 `-_.:~`）讓重送安全：同一租戶、同一 key 在視窗內重送，"
                + "回放第一次的回應（加 `Idempotent-Replayed: true`）且不再計量；同一個 key 帶不同的請求內容回 422。"
                + "只有成功的回應會被記住，錯誤（4xx／5xx）不會，重送會重新處理。")
            .Produces<GroundResultDto>()
            .ProducesProblem(StatusCodes.Status400BadRequest)
            .ProducesProblem(StatusCodes.Status422UnprocessableEntity)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        return v1;
    }

    private static async Task<IResult> GroundAsync(
        GroundRequestDto body,
        [FromHeader(Name = IdempotencyKeyHeader)] string? idempotencyKey,
        HttpContext http,
        ConsultationService.ConsultationServiceClient client,
        IOptions<ConsultOptions> options,
        IUsageRecorder usage,
        IIdempotencyStore idempotency,
        IOptions<Microsoft.AspNetCore.Http.Json.JsonOptions> json,
        CancellationToken ct)
    {
        byte[]? fingerprint = null;
        if (idempotencyKey is not null)
        {
            if (!ValidKey().IsMatch(idempotencyKey))
            {
                return Results.Problem(
                    $"{IdempotencyKeyHeader} must be 1-{IIdempotencyStore.KeyLength} characters from A-Z, a-z, 0-9 and -_.:~.",
                    statusCode: StatusCodes.Status400BadRequest);
            }

            fingerprint = Fingerprint(body);
            var seen = await idempotency.LookupAsync(GroundOperation, idempotencyKey, fingerprint, ct);
            if (seen.State != IdempotencyState.Fresh)
            {
                return Seen(http, seen);
            }
        }

        GroundResponse response;
        try
        {
            response = await client.GroundAsync(
                new GroundRequest { Question = body.Question ?? string.Empty, AsOf = body.PointInTime ?? string.Empty },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: ct);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return Results.Problem(ex.Status.Detail, statusCode: StatusCodes.Status400BadRequest);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return Results.Problem("Consult service unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
        }

        var dto = ToDto(response);
        if (idempotencyKey is null)
        {
            await usage.RecordAsync(GroundOperation, 1, ct);
            return Results.Ok(dto);
        }

        // 第一次與回放都送同一份文字，位元組相同。
        var text = JsonSerializer.Serialize(dto, json.Value.SerializerOptions);
        var stored = await idempotency.CompleteAsync(GroundOperation, idempotencyKey, fingerprint!, text, GroundOperation, 1, ct);
        return stored.State == IdempotencyState.Fresh ? Results.Content(text, "application/json") : Seen(http, stored);
    }

    /// <summary>key 已出現過：內容相同就回放第一次的回應，不同就 422。</summary>
    private static IResult Seen(HttpContext http, IdempotencyResult seen)
    {
        if (seen.State == IdempotencyState.Mismatch)
        {
            return Results.Problem(
                $"This {IdempotencyKeyHeader} was already used with a different request.",
                statusCode: StatusCodes.Status422UnprocessableEntity);
        }

        http.Response.Headers[ReplayedHeader] = "true";
        return Results.Content(seen.Response, "application/json");
    }

    /// <summary>請求內容的雜湊（只放會影響結果的欄位；省略與空字串同義，與送給 consult 的一致）。</summary>
    private static byte[] Fingerprint(GroundRequestDto body) =>
        SHA256.HashData(Encoding.UTF8.GetBytes($"{body.Question ?? string.Empty}\0{body.PointInTime ?? string.Empty}"));

    [GeneratedRegex(@"^[A-Za-z0-9_.:~-]{1,255}$")]
    private static partial Regex ValidKey();

    private static GroundResultDto ToDto(GroundResponse response) => response.OutcomeCase switch
    {
        GroundResponse.OutcomeOneofCase.Evidence => new GroundResultDto(
            "grounded",
            [.. response.Evidence.Citations.Select(c => new AuthorityDto(c.Code, c.Iri, c.Label, c.Text))],
            response.Evidence.Confidence switch
            {
                Confidence.High => "direct",
                Confidence.Low => "indirect",
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
                    Refused.Types.Reason.NoEvidence => "no_authority",
                    Refused.Types.Reason.OutOfScope => "out_of_scope",
                    Refused.Types.Reason.MissingInput => "missing_input",
                    Refused.Types.Reason.LocalLawUncovered => "local_law_uncovered",
                    _ => throw new InvalidOperationException($"Unexpected refusal reason {response.Refused.Reason}."),
                },
                response.Refused.Detail)),
        _ => throw new InvalidOperationException("Consult returned neither evidence nor a refusal."),
    };
}