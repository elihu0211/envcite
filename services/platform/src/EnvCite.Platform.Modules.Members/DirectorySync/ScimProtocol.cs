using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>SCIM 2.0（RFC 7643／7644）的常數與回應的寫法。</summary>
internal static class Scim
{
    public const string ContentType = "application/scim+json";
    public const string UserSchema = "urn:ietf:params:scim:schemas:core:2.0:User";
    public const string ListSchema = "urn:ietf:params:scim:api:messages:2.0:ListResponse";
    public const string ErrorSchema = "urn:ietf:params:scim:api:messages:2.0:Error";
    public const string PatchSchema = "urn:ietf:params:scim:api:messages:2.0:PatchOp";
    public const string ConfigSchema = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig";
    public const string ResourceTypeSchema = "urn:ietf:params:scim:schemas:core:2.0:ResourceType";
    public const string SchemaSchema = "urn:ietf:params:scim:schemas:core:2.0:Schema";

    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}

internal sealed record ScimName(string? Formatted);

internal sealed record ScimEmail(string Value, bool Primary, string Type);

internal sealed record ScimMeta(string ResourceType, DateTimeOffset? LastModified, string Location);

internal sealed record ScimUser(
    string[] Schemas, string Id, string? ExternalId, string UserName, ScimName? Name, string? DisplayName, ScimEmail[] Emails, bool Active, ScimMeta Meta);

internal sealed record ScimList<T>(
    string[] Schemas, int TotalResults, int StartIndex, int ItemsPerPage, [property: JsonPropertyName("Resources")] IReadOnlyList<T> Resources);

internal sealed record ScimError(string[] Schemas, string Status, string? ScimType, string Detail, string? Code);

/// <summary>寫出 SCIM 回應：內容型別 <c>application/scim+json</c>，錯誤帶 RFC 7644 §3.12 的欄位加本倉庫穩定的 <c>code</c>。</summary>
internal static class ScimResponses
{
    public static Task WriteErrorAsync(HttpContext context, int status, string? scimType, string detail, string? code) =>
        context.Response.WriteAsJsonAsync(
            new ScimError([Scim.ErrorSchema], status.ToString(System.Globalization.CultureInfo.InvariantCulture), scimType, detail, code),
            Scim.Json,
            Scim.ContentType);

    public static IResult Error(int status, string? scimType, string detail, string? code = null) =>
        new ScimResult(status, new ScimError([Scim.ErrorSchema], status.ToString(System.Globalization.CultureInfo.InvariantCulture), scimType, detail, code));

    /// <summary>失敗的結果代碼對映成 HTTP 狀態與 SCIM 的 <c>scimType</c>；<c>detail</c> 永遠是英文（來自 <see cref="OutcomeCodes"/>），另帶 <c>code</c>。</summary>
    public static IResult Error<T>(AdminOutcome<T> outcome)
    {
        var (status, scimType) = outcome.Code switch
        {
            OutcomeCodes.DirectoryNotEnabled or OutcomeCodes.MemberNotFound or OutcomeCodes.DirectoryTokenNotFound => (StatusCodes.Status404NotFound, null),
            OutcomeCodes.MemberEmailTaken or OutcomeCodes.DirectoryExternalIdTaken or OutcomeCodes.DirectoryAdoptRequired => (StatusCodes.Status409Conflict, "uniqueness"),
            OutcomeCodes.DirectoryLastOwner => (StatusCodes.Status409Conflict, null),
            OutcomeCodes.DirectoryEmailImmutable => (StatusCodes.Status400BadRequest, "mutability"),
            OutcomeCodes.MailNotEnabled => (StatusCodes.Status503ServiceUnavailable, null),
            OutcomeCodes.DirectoryRoleNotMappable => (StatusCodes.Status403Forbidden, null),
            _ => outcome.Status switch
            {
                AdminStatus.NotFound => (StatusCodes.Status404NotFound, null),
                AdminStatus.Conflict => (StatusCodes.Status409Conflict, null),
                AdminStatus.Forbidden => (StatusCodes.Status403Forbidden, null),
                AdminStatus.Unavailable => (StatusCodes.Status503ServiceUnavailable, null),
                _ => (StatusCodes.Status400BadRequest, (string?)"invalidValue"),
            },
        };
        return Error(status, scimType, outcome.Message ?? "Request failed.", outcome.Code);
    }

    /// <summary>結果的分類，指標的 <c>outcome</c> 標籤（有限集合）。</summary>
    public static string Category<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.Ok => "ok",
        AdminStatus.NotFound => "not_found",
        AdminStatus.Conflict => "conflict",
        AdminStatus.Forbidden => "forbidden",
        AdminStatus.Unavailable => "unavailable",
        _ => "invalid",
    };
}

/// <summary>一個 SCIM 回應：狀態、內容型別與（建立時的）<c>Location</c>。</summary>
internal sealed class ScimResult(int status, object? body, string? location = null) : IResult
{
    public async Task ExecuteAsync(HttpContext httpContext)
    {
        httpContext.Response.StatusCode = status;
        if (location is not null)
        {
            httpContext.Response.Headers.Location = location;
        }

        if (body is not null)
        {
            await httpContext.Response.WriteAsJsonAsync(body, body.GetType(), Scim.Json, Scim.ContentType);
        }
    }
}

/// <summary>讀進來的 SCIM 使用者與 PATCH：寬鬆地接受身分來源各家的寫法（屬性名稱不分大小寫、布林可能是字串），不認得的屬性略過。</summary>
internal static partial class ScimInput
{
    public sealed record User(string Email, string? DisplayName, string? ExternalId, bool? Active);

    /// <summary>屬性名稱不分大小寫（SCIM 屬性名稱本來就不分）。</summary>
    public static bool TryGet(JsonElement obj, string name, out JsonElement value)
    {
        if (obj.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in obj.EnumerateObject())
            {
                if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase))
                {
                    value = property.Value;
                    return true;
                }
            }
        }

        value = default;
        return false;
    }

    /// <summary>Entra 送字串 <c>"True"</c>／<c>"False"</c>，其他送布林；兩種都收。</summary>
    public static bool? Bool(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.String when bool.TryParse(value.GetString(), out var parsed) => parsed,
        _ => null,
    };

    public static string? Text(JsonElement value) => value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    /// <summary>POST／PUT 的使用者。<c>userName</c> 是 email；它不是 email 時退而用 <c>emails</c> 的主要（或第一個）位址。找不到 email 回 null。</summary>
    public static User? ParseUser(JsonElement body)
    {
        if (body.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var userName = TryGet(body, "userName", out var u) ? Text(u)?.Trim() : null;
        var email = EmailAddresses.IsValid(userName ?? string.Empty) ? userName : FirstEmail(body);
        if (email is null)
        {
            return null;
        }

        var active = TryGet(body, "active", out var a) ? Bool(a) : null;
        var externalId = TryGet(body, "externalId", out var e) ? Text(e) : null;
        return new User(email, DisplayNameOf(body), externalId, active);
    }

    private static string? FirstEmail(JsonElement body)
    {
        if (!TryGet(body, "emails", out var emails) || emails.ValueKind != JsonValueKind.Array)
        {
            return null;
        }

        string? first = null;
        foreach (var entry in emails.EnumerateArray())
        {
            if (!TryGet(entry, "value", out var v) || Text(v)?.Trim() is not { } value || !EmailAddresses.IsValid(value))
            {
                continue;
            }

            first ??= value;
            if (TryGet(entry, "primary", out var p) && Bool(p) == true)
            {
                return value;
            }
        }

        return first;
    }

    /// <summary><c>displayName</c>，沒有就 <c>name.formatted</c>，再沒有就名加姓。</summary>
    public static string? DisplayNameOf(JsonElement body)
    {
        if (TryGet(body, "displayName", out var display) && Text(display) is { Length: > 0 } shown)
        {
            return shown;
        }

        return TryGet(body, "name", out var name) ? NameText(name) : null;
    }

    private static string? NameText(JsonElement name)
    {
        if (TryGet(name, "formatted", out var formatted) && Text(formatted) is { Length: > 0 } text)
        {
            return text;
        }

        var given = TryGet(name, "givenName", out var g) ? Text(g) : null;
        var family = TryGet(name, "familyName", out var f) ? Text(f) : null;
        var joined = string.Join(' ', new[] { given, family }.Where(part => !string.IsNullOrWhiteSpace(part)));
        return joined.Length == 0 ? null : joined;
    }

    /// <summary>PATCH（RFC 7644 §3.5.2）：<c>add</c>／<c>replace</c>／<c>remove</c>，有 path 或沒有（值是一個物件）。</summary>
    public static (DirectoryPatch? Patch, IResult? Failure) ParsePatch(JsonElement body)
    {
        if (!TryGet(body, "Operations", out var operations) || operations.ValueKind != JsonValueKind.Array || operations.GetArrayLength() == 0)
        {
            return (null, ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "Operations must be a non-empty array."));
        }

        string? displayName = null;
        bool? active = null;
        string? externalId = null;
        string? email = null;

        foreach (var operation in operations.EnumerateArray())
        {
            var op = (TryGet(operation, "op", out var o) ? Text(o) : null)?.ToLowerInvariant();
            if (op is not ("add" or "replace" or "remove"))
            {
                return (null, ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "op must be add, replace or remove."));
            }

            var path = TryGet(operation, "path", out var p) ? Text(p) : null;
            TryGet(operation, "value", out var value);
            if (string.IsNullOrEmpty(path))
            {
                if (value.ValueKind != JsonValueKind.Object)
                {
                    return (null, ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "A value object is required when path is missing."));
                }

                foreach (var property in value.EnumerateObject())
                {
                    if (!Apply(property.Name, op, property.Value, ref displayName, ref active, ref externalId, ref email, out var failure))
                    {
                        return (null, failure);
                    }
                }
            }
            else if (!Apply(path, op, value, ref displayName, ref active, ref externalId, ref email, out var failure))
            {
                return (null, failure);
            }
        }

        return (new DirectoryPatch(displayName, active, null, externalId, email), null);
    }

    private static bool Apply(
        string path, string op, JsonElement value, ref string? displayName, ref bool? active, ref string? externalId, ref string? email,
        out IResult? failure)
    {
        failure = null;
        var name = path.StartsWith(Scim.UserSchema + ":", StringComparison.OrdinalIgnoreCase) ? path[(Scim.UserSchema.Length + 1)..] : path;
        switch (name.ToLowerInvariant())
        {
            case "active":
                if (op != "remove")
                {
                    active = Bool(value);
                    if (active is null)
                    {
                        failure = ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidValue", "active must be a boolean.");
                        return false;
                    }
                }

                break;
            case "displayname" or "name.formatted":
                displayName = op == "remove" ? string.Empty : Text(value);
                break;
            case "name":
                displayName = op == "remove" ? string.Empty : NameText(value) ?? displayName;
                break;
            case "externalid":
                externalId = op == "remove" ? externalId : Text(value);
                break;
            case "username":
                if (op == "remove")
                {
                    failure = ScimResponses.Error(StatusCodes.Status400BadRequest, "mutability", "userName cannot be removed.");
                    return false;
                }

                email = Text(value)?.Trim();
                break;
            default:
                // 不認得或不支援的屬性（地址、職稱、emails 等）：略過，不讓一個多餘的屬性使整個同步失敗。
                break;
        }

        return true;
    }

    /// <summary>過濾只支援 <c>userName eq "…"</c> 與 <c>externalId eq "…"</c>（身分來源比對用）；其他回 <c>invalidFilter</c>。</summary>
    public static (string? ExternalId, string? Email, bool Valid) ParseFilter(string? filter)
    {
        if (string.IsNullOrWhiteSpace(filter))
        {
            return (null, null, true);
        }

        var match = EqualityFilter().Match(filter);
        if (!match.Success)
        {
            return (null, null, false);
        }

        var value = match.Groups["value"].Value.Replace("\\\"", "\"", StringComparison.Ordinal).Replace("\\\\", "\\", StringComparison.Ordinal);
        return string.Equals(match.Groups["attribute"].Value, "externalId", StringComparison.OrdinalIgnoreCase)
            ? (value, null, true)
            : (null, value, true);
    }

    [GeneratedRegex("^\\s*(?<attribute>userName|externalId)\\s+eq\\s+\"(?<value>(?:[^\"\\\\]|\\\\.)*)\"\\s*$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant, matchTimeoutMilliseconds: 1000)]
    private static partial Regex EqualityFilter();
}