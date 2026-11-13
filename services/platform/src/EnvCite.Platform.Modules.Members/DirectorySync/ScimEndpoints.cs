using System.Globalization;
using System.Text.Json;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 目錄同步的入口：SCIM 2.0 的 <c>/scim/v2</c>（RFC 7643／7644）。憑證是租戶的目錄同步 token（<c>scim_…</c>），租戶只來自 token；
/// 不在 <c>/v2</c> 之下，所以不計量、不走方案與逐租戶限流，也不進 <c>openapi_v2.json</c>（規格是 RFC 本身，由
/// <c>/ServiceProviderConfig</c> 自述能力）。部署者沒開功能面時不掛路徑。
/// </summary>
/// <remarks>
/// 支援 Users 的 GET（含 <c>userName eq</c>／<c>externalId eq</c> 過濾與分頁）／POST／PUT／PATCH／DELETE 與三個 discovery 端點；
/// 不支援 Bulk、排序、ETag、改密碼、Groups（回 404）。DELETE 是停用，不是刪除。
/// </remarks>
public static class ScimEndpoints
{
    /// <summary>本文上限：一個使用者的 JSON 遠小於這個數字。</summary>
    private const long MaxBodyBytes = 256 * 1024;

    public static IEndpointRouteBuilder MapDirectoryScim(this IEndpointRouteBuilder app)
    {
        if (!app.ServiceProvider.GetRequiredService<IOptions<DirectoryOptions>>().Value.Enabled)
            return app;

        var scim = app.MapGroup(ScimPaths.Prefix)
            .RequireAuthorization(PlatformAuth.DirectoryPolicy)
            .ExcludeFromDescription();

        // 授權之後、端點之前：沒有有效 token 的請求到不了這裡，不耗用額度。
        scim.AddEndpointFilter(async (invocation, next) =>
        {
            var context = invocation.HttpContext;
            var limiter = context.RequestServices.GetRequiredService<DirectoryRateLimiter>();
            if (limiter.TryAcquire(context.User.FindFirst(PlatformClaimTypes.DirectoryTokenId)?.Value ?? "anonymous", out var retryAfter))
                return await next(invocation);

            context.RequestServices.GetRequiredService<DirectoryMetrics>().Record("other", "rate_limited");
            context.Response.Headers.RetryAfter = retryAfter.ToString(CultureInfo.InvariantCulture);
            return ScimResponses.Error(StatusCodes.Status429TooManyRequests, null, "Too many requests. Retry after the time given in Retry-After.");
        });

        MapUsers(scim);
        MapGroups(scim);
        MapDiscovery(scim);

        // 沒有的資源（Groups、Bulk…）：已驗證的呼叫端得到 SCIM 格式的 404，而不是框架的空回應。
        scim.Map("/{**rest}", (DirectoryMetrics metrics) =>
        {
            metrics.Record("other", "not_found");
            return ScimResponses.Error(StatusCodes.Status404NotFound, null, "This resource type is not supported. Supported: Users and Groups.");
        });

        return app;
    }

    private static void MapUsers(RouteGroupBuilder scim)
    {
        scim.MapGet("/Users", async (
            HttpContext context, string? filter, int? startIndex, int? count,
            IDirectoryProvisioning directory, IOptions<DirectoryOptions> options, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var (externalId, email, valid) = ScimInput.ParseFilter(filter, "userName");
            if (!valid)
            {
                metrics.Record("list", "invalid");
                return ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidFilter", "Only 'userName eq \"…\"' and 'externalId eq \"…\"' filters are supported.");
            }

            var skip = Math.Max(startIndex ?? 1, 1) - 1;
            var take = Math.Max(count ?? options.Value.MaxPageSize, 0);
            return await RunAsync(
                metrics, "list", () => directory.ListAsync(context.User, skip, take, externalId, email, cancellationToken),
                page => new ScimResult(
                    StatusCodes.Status200OK,
                    new ScimList<ScimUser>([Scim.ListSchema], page.Total, skip + 1, page.Items.Count, [.. page.Items.Select(m => ToScim(m, context.Request))])));
        });

        scim.MapGet("/Users/{id}", (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
            RunAsync(
                metrics, "get", () => directory.FindAsync(context.User, id, cancellationToken),
                member => new ScimResult(StatusCodes.Status200OK, ToScim(member, context.Request))));

        scim.MapPost("/Users", async (
            HttpContext context, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var read = await ReadUserAsync(context, metrics, "create", cancellationToken);
            if (read.User is not { } user)
                return read.Failure!;

            return await RunAsync(
                metrics, "create",
                () => directory.ProvisionAsync(context.User, new DirectoryProvision(user.Email, user.DisplayName, user.ExternalId, null, user.Active ?? true), cancellationToken),
                member => new ScimResult(StatusCodes.Status201Created, ToScim(member, context.Request), Location(context.Request, member.Id)));
        });

        scim.MapPut("/Users/{id}", async (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var read = await ReadUserAsync(context, metrics, "update", cancellationToken);
            if (read.User is not { } user)
                return read.Failure!;

            // PUT 是取代：沒送的顯示名稱視為清掉，沒送 active 視為啟用。email 與外部識別碼不能清。
            var patch = new DirectoryPatch(user.DisplayName ?? string.Empty, user.Active ?? true, null, user.ExternalId, user.Email);
            return await RunAsync(
                metrics, "update", () => directory.UpdateAsync(context.User, id, patch, cancellationToken),
                member => new ScimResult(StatusCodes.Status200OK, ToScim(member, context.Request)));
        });

        scim.MapPatch("/Users/{id}", async (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            if (await ReadBodyAsync(context, cancellationToken) is not { } body)
            {
                metrics.Record("update", "invalid");
                return ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "The request body must be valid JSON.");
            }

            var (patch, failure) = ScimInput.ParsePatch(body);
            if (patch is null)
            {
                metrics.Record("update", "invalid");
                return failure!;
            }

            return await RunAsync(
                metrics, "update", () => directory.UpdateAsync(context.User, id, patch, cancellationToken),
                member => new ScimResult(StatusCodes.Status200OK, ToScim(member, context.Request)));
        });

        scim.MapDelete("/Users/{id}", (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
            RunAsync(
                metrics, "delete", () => directory.DeactivateAsync(context.User, id, cancellationToken),
                _ => new ScimResult(StatusCodes.Status204NoContent, null)));
    }

    private static void MapGroups(RouteGroupBuilder scim)
    {
        scim.MapGet("/Groups", async (
            HttpContext context, string? filter, int? startIndex, int? count,
            IDirectoryProvisioning directory, IOptions<DirectoryOptions> options, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var (externalId, name, valid) = ScimInput.ParseFilter(filter, "displayName");
            if (!valid)
            {
                metrics.Record("group_list", "invalid");
                return ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidFilter", "Only 'displayName eq \"…\"' and 'externalId eq \"…\"' filters are supported.");
            }

            var skip = Math.Max(startIndex ?? 1, 1) - 1;
            var take = Math.Max(count ?? options.Value.MaxPageSize, 0);
            return await RunAsync(
                metrics, "group_list", () => directory.ListGroupsAsync(context.User, skip, take, externalId, name, cancellationToken),
                page => new ScimResult(
                    StatusCodes.Status200OK,
                    new ScimList<ScimGroup>([Scim.ListSchema], page.Total, skip + 1, page.Items.Count, [.. page.Items.Select(g => ToScim(g, context.Request))])));
        });

        scim.MapGet("/Groups/{id}", (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
            RunGroupAsync(
                metrics, "group_get", id, groupId => directory.FindGroupAsync(context.User, groupId, cancellationToken),
                group => new ScimResult(StatusCodes.Status200OK, ToScim(group, context.Request))));

        scim.MapPost("/Groups", async (
            HttpContext context, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var read = await ReadGroupAsync(context, metrics, "group_create", cancellationToken);
            if (read.Group is not { } body)
                return read.Failure!;

            return await RunAsync(
                metrics, "group_create",
                () => directory.CreateGroupAsync(context.User, new DirectoryGroupInput(body.DisplayName ?? string.Empty, body.ExternalId, body.Members), cancellationToken),
                group => new ScimResult(StatusCodes.Status201Created, ToScim(group, context.Request), GroupLocation(context.Request, group.Id)));
        });

        scim.MapPut("/Groups/{id}", async (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            var read = await ReadGroupAsync(context, metrics, "group_update", cancellationToken);
            if (read.Group is not { } body)
                return read.Failure!;

            // PUT 是取代：成員整組換成送來的（沒送就是清空）。
            var patch = new DirectoryGroupPatch(
                body.DisplayName, body.ExternalId, [new DirectoryGroupMemberChange(GroupMemberOp.Replace, body.Members ?? [])]);
            return await RunGroupAsync(
                metrics, "group_update", id, groupId => directory.UpdateGroupAsync(context.User, groupId, patch, cancellationToken),
                group => new ScimResult(StatusCodes.Status200OK, ToScim(group, context.Request)));
        });

        scim.MapPatch("/Groups/{id}", async (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
        {
            if (await ReadBodyAsync(context, cancellationToken) is not { } body)
            {
                metrics.Record("group_update", "invalid");
                return ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "The request body must be valid JSON.");
            }

            var (patch, failure) = ScimInput.ParseGroupPatch(body);
            if (patch is null)
            {
                metrics.Record("group_update", "invalid");
                return failure!;
            }

            return await RunGroupAsync(
                metrics, "group_update", id, groupId => directory.UpdateGroupAsync(context.User, groupId, patch, cancellationToken),
                group => new ScimResult(StatusCodes.Status200OK, ToScim(group, context.Request)));
        });

        scim.MapDelete("/Groups/{id}", (
            HttpContext context, string id, IDirectoryProvisioning directory, DirectoryMetrics metrics, CancellationToken cancellationToken) =>
            RunGroupAsync(
                metrics, "group_delete", id, groupId => directory.DeleteGroupAsync(context.User, groupId, cancellationToken),
                _ => new ScimResult(StatusCodes.Status204NoContent, null)));
    }

    private static void MapDiscovery(RouteGroupBuilder scim)
    {
        scim.MapGet("/ServiceProviderConfig", (HttpContext context, IOptions<DirectoryOptions> options, DirectoryMetrics metrics) =>
        {
            metrics.Record("discovery", "ok");
            return new ScimResult(StatusCodes.Status200OK, new
            {
                schemas = new[] { Scim.ConfigSchema },
                patch = new { supported = true },
                bulk = new { supported = false, maxOperations = 0, maxPayloadSize = 0 },
                filter = new { supported = true, maxResults = options.Value.MaxPageSize },
                changePassword = new { supported = false },
                sort = new { supported = false },
                etag = new { supported = false },
                authenticationSchemes = new[]
                {
                    new
                    {
                        type = "oauthbearertoken",
                        name = "Directory sync token",
                        description = "Authentication with a tenant's directory sync token: Authorization: Bearer scim_…",
                        specUri = "https://www.rfc-editor.org/info/rfc6750",
                        primary = true,
                    },
                },
                meta = new { resourceType = "ServiceProviderConfig", location = Absolute(context.Request, "/ServiceProviderConfig") },
            });
        });

        scim.MapGet("/ResourceTypes", (HttpContext context, DirectoryMetrics metrics) =>
        {
            metrics.Record("discovery", "ok");
            return new ScimResult(StatusCodes.Status200OK, new ScimList<object>([Scim.ListSchema], 2, 1, 2, [UserResourceType(context.Request), GroupResourceType(context.Request)]));
        });

        scim.MapGet("/ResourceTypes/{id}", (HttpContext context, string id, DirectoryMetrics metrics) =>
            DiscoveryItem(
                metrics,
                string.Equals(id, "User", StringComparison.OrdinalIgnoreCase) ? UserResourceType(context.Request)
                : string.Equals(id, "Group", StringComparison.OrdinalIgnoreCase) ? GroupResourceType(context.Request)
                : null));

        scim.MapGet("/Schemas", (DirectoryMetrics metrics) =>
        {
            metrics.Record("discovery", "ok");
            return new ScimResult(StatusCodes.Status200OK, new ScimList<object>([Scim.ListSchema], 2, 1, 2, [UserSchema(), GroupSchema()]));
        });

        scim.MapGet("/Schemas/{id}", (string id, DirectoryMetrics metrics) =>
            DiscoveryItem(
                metrics,
                string.Equals(id, Scim.UserSchema, StringComparison.OrdinalIgnoreCase) ? UserSchema()
                : string.Equals(id, Scim.GroupSchema, StringComparison.OrdinalIgnoreCase) ? GroupSchema()
                : null));
    }

    private static IResult DiscoveryItem(DirectoryMetrics metrics, object? item)
    {
        metrics.Record("discovery", item is null ? "not_found" : "ok");
        return item is null
            ? ScimResponses.Error(StatusCodes.Status404NotFound, null, "Not found.")
            : new ScimResult(StatusCodes.Status200OK, item);
    }

    private static object UserResourceType(HttpRequest request) => new
    {
        schemas = new[] { Scim.ResourceTypeSchema },
        id = "User",
        name = "User",
        endpoint = "/Users",
        description = "A member of the tenant.",
        schema = Scim.UserSchema,
        meta = new { resourceType = "ResourceType", location = Absolute(request, "/ResourceTypes/User") },
    };

    private static object GroupResourceType(HttpRequest request) => new
    {
        schemas = new[] { Scim.ResourceTypeSchema },
        id = "Group",
        name = "Group",
        endpoint = "/Groups",
        description = "A group of members. A group the tenant's owner mapped to a role decides the role of its members.",
        schema = Scim.GroupSchema,
        meta = new { resourceType = "ResourceType", location = Absolute(request, "/ResourceTypes/Group") },
    };

    private static object GroupSchema() => new
    {
        schemas = new[] { Scim.SchemaSchema },
        id = Scim.GroupSchema,
        name = "Group",
        description = "Members must be users that directory sync manages. The role a group grants is configured by the tenant's owner, not through SCIM.",
        attributes = new object[]
        {
            AttributeOf("displayName", "string", required: true, mutability: "readWrite", uniqueness: "server"),
            AttributeOf("externalId", "string", required: false, mutability: "readWrite", uniqueness: "none"),
        },
    };

    private static object UserSchema() => new
    {
        schemas = new[] { Scim.SchemaSchema },
        id = Scim.UserSchema,
        name = "User",
        description = "A member of the tenant. userName is the member's email; it cannot be changed. Roles are not set through SCIM.",
        attributes = new object[]
        {
            AttributeOf("userName", "string", required: true, mutability: "immutable", uniqueness: "server"),
            AttributeOf("externalId", "string", required: false, mutability: "readWrite", uniqueness: "none"),
            AttributeOf("displayName", "string", required: false, mutability: "readWrite", uniqueness: "none"),
            AttributeOf("active", "boolean", required: false, mutability: "readWrite", uniqueness: "none"),
        },
    };

    private static object AttributeOf(string name, string type, bool required, string mutability, string uniqueness) => new
    {
        name,
        type,
        multiValued = false,
        required,
        caseExact = false,
        mutability,
        returned = "default",
        uniqueness,
    };

    /// <summary>讀本文並解析成使用者；失敗時回應已記進指標。</summary>
    private static async Task<(ScimInput.User? User, IResult? Failure)> ReadUserAsync(
        HttpContext context, DirectoryMetrics metrics, string operation, CancellationToken cancellationToken)
    {
        if (await ReadBodyAsync(context, cancellationToken) is not { } body)
        {
            metrics.Record(operation, "invalid");
            return (null, ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "The request body must be valid JSON."));
        }

        if (ScimInput.ParseUser(body) is not { } user)
        {
            metrics.Record(operation, "invalid");
            return (null, ScimResponses.Error(AdminOutcome.Invalid<bool>(OutcomeCodes.EmailInvalid)));
        }

        return (user, null);
    }

    /// <summary>讀本文並解析成群組；失敗時回應已記進指標。</summary>
    private static async Task<(ScimInput.GroupBody? Group, IResult? Failure)> ReadGroupAsync(
        HttpContext context, DirectoryMetrics metrics, string operation, CancellationToken cancellationToken)
    {
        if (await ReadBodyAsync(context, cancellationToken) is not { } body)
        {
            metrics.Record(operation, "invalid");
            return (null, ScimResponses.Error(StatusCodes.Status400BadRequest, "invalidSyntax", "The request body must be valid JSON."));
        }

        if (ScimInput.ParseGroup(body) is not { DisplayName: { Length: > 0 } } group)
        {
            metrics.Record(operation, "invalid");
            return (null, ScimResponses.Error(AdminOutcome.Invalid<bool>(OutcomeCodes.DirectoryGroupInvalid)));
        }

        return (group, null);
    }

    /// <summary>路徑上的群組 Id 不是 Guid 就是找不到（來自呼叫端，不能讓亂打的值變成 500）。</summary>
    private static async Task<IResult> RunGroupAsync<T>(
        DirectoryMetrics metrics, string operation, string id, Func<Guid, Task<AdminOutcome<T>>> action, Func<T, IResult> ok)
    {
        if (!Guid.TryParse(id, out var groupId))
        {
            metrics.Record(operation, "not_found");
            return ScimResponses.Error(AdminOutcome.NotFound<T>(OutcomeCodes.DirectoryGroupNotFound));
        }

        return await RunAsync(metrics, operation, () => action(groupId), ok);
    }

    private static async Task<JsonElement?> ReadBodyAsync(HttpContext context, CancellationToken cancellationToken)
    {
        if (context.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
            limit.MaxRequestBodySize = MaxBodyBytes;

        try
        {
            using var document = await JsonDocument.ParseAsync(context.Request.Body, cancellationToken: cancellationToken);
            return document.RootElement.Clone();
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static async Task<IResult> RunAsync<T>(
        DirectoryMetrics metrics, string operation, Func<Task<AdminOutcome<T>>> action, Func<T, IResult> ok)
    {
        var outcome = await action();
        metrics.Record(operation, ScimResponses.Category(outcome));
        return outcome.IsOk ? ok(outcome.Value!) : ScimResponses.Error(outcome);
    }

    private static ScimUser ToScim(DirectoryMember member, HttpRequest request) => new(
        [Scim.UserSchema],
        member.Id,
        member.ExternalId,
        member.Email,
        member.DisplayName is null ? null : new ScimName(member.DisplayName),
        member.DisplayName,
        [new ScimEmail(member.Email, true, "work")],
        member.Active,
        new ScimMeta("User", member.SyncedAt, Location(request, member.Id)));

    private static ScimGroup ToScim(DirectoryGroupDetail group, HttpRequest request) => new(
        [Scim.GroupSchema],
        group.Id.ToString(),
        group.ExternalId,
        group.DisplayName,
        [.. group.Members.Select(id => new ScimGroupMember(id, Location(request, id), "User"))],
        new ScimMeta("Group", null, GroupLocation(request, group.Id)));

    private static string GroupLocation(HttpRequest request, Guid id) => Absolute(request, "/Groups/" + id);

    private static string Location(HttpRequest request, string id) => Absolute(request, "/Users/" + Uri.EscapeDataString(id));

    private static string Absolute(HttpRequest request, string path) => $"{request.Scheme}://{request.Host}{request.PathBase}{ScimPaths.Prefix}{path}";
}