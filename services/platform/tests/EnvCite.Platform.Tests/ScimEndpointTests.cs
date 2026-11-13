using System.Diagnostics.Metrics;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

using EnvCite.Platform.Modules.Members.DirectorySync;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 目錄同步的入口（SCIM 2.0，RFC 7643／7644）：token 認證、錯誤格式、冪等、過濾與分頁、PATCH 的各家寫法、
/// 跨租戶、限流與指標。成員規則本身（角色上限、最後一位擁有者…）在 <see cref="DirectorySyncTests"/>，這裡只驗 HTTP 面。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class ScimEndpointTests(PlatformFactory factory) : DirectoryTestBase(factory)
{
    private const string Users = "/scim/v2/Users";

    private const string ScimContentType = "application/scim+json";

    private const string UserSchema = "urn:ietf:params:scim:schemas:core:2.0:User";

    private const string GroupSchema = "urn:ietf:params:scim:schemas:core:2.0:Group";

    private const string PatchSchema = "urn:ietf:params:scim:api:messages:2.0:PatchOp";

    private sealed record Reply(
        HttpStatusCode Status, string? ContentType, string? Location, string? RetryAfter, string? Challenge, JsonElement Body)
    {
        public string Detail => Body.GetProperty("detail").GetString()!;

        public string? ScimType => Body.TryGetProperty("scimType", out var type) ? type.GetString() : null;

        public string? Code => Body.TryGetProperty("code", out var code) ? code.GetString() : null;
    }

    private async Task<Reply> SendAsync(
        HttpMethod method, string path, string? token, string? json = null, HttpClient? client = null)
    {
        client ??= Factory.CreateClient();
        using var request = new HttpRequestMessage(method, path);
        if (token is not null)
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

        if (json is not null)
            request.Content = new StringContent(json, Encoding.UTF8, ScimContentType);

        using var response = await client.SendAsync(request, Ct);
        var text = await response.Content.ReadAsStringAsync(Ct);
        return new Reply(
            response.StatusCode,
            response.Content.Headers.ContentType?.MediaType,
            response.Headers.Location?.ToString(),
            response.Headers.TryGetValues("Retry-After", out var retry) ? retry.First() : null,
            response.Headers.WwwAuthenticate.FirstOrDefault()?.ToString(),
            string.IsNullOrEmpty(text) ? default : JsonElement.Parse(text));
    }

    private static string NewUser(string email, string? externalId = null, string? extra = null) =>
        $$"""
        {"schemas":["{{UserSchema}}"],"userName":"{{email}}","externalId":"{{externalId ?? $"ext-{Guid.NewGuid():N}"}}","name":{"givenName":"Ann","familyName":"Lee"},
         "emails":[{"value":"{{email}}","primary":true,"type":"work"}],"active":true{{extra}}}
        """;

    private static string Patch(string operations) => $$"""{"schemas":["{{PatchSchema}}"],"Operations":{{operations}}}""";

    private async Task<Reply> CreateAsync(Org org, string? email = null, string? externalId = null, string? extra = null) =>
        await SendAsync(HttpMethod.Post, Users, org.Token, NewUser(email ?? NewEmail(), externalId, extra));

    [Fact]
    public async Task DiscoveryDescribesWhatTheServerSupports()
    {
        var org = await OrgAsync("scim-a");

        var config = await SendAsync(HttpMethod.Get, "/scim/v2/ServiceProviderConfig", org.Token);
        var types = await SendAsync(HttpMethod.Get, "/scim/v2/ResourceTypes", org.Token);
        var schemas = await SendAsync(HttpMethod.Get, "/scim/v2/Schemas", org.Token);

        Assert.Equal(HttpStatusCode.OK, config.Status);
        Assert.Equal(ScimContentType, config.ContentType);
        Assert.True(config.Body.GetProperty("patch").GetProperty("supported").GetBoolean());
        Assert.False(config.Body.GetProperty("bulk").GetProperty("supported").GetBoolean());
        Assert.False(config.Body.GetProperty("sort").GetProperty("supported").GetBoolean());
        Assert.False(config.Body.GetProperty("changePassword").GetProperty("supported").GetBoolean());
        Assert.True(config.Body.GetProperty("filter").GetProperty("supported").GetBoolean());
        Assert.Equal(100, config.Body.GetProperty("filter").GetProperty("maxResults").GetInt32());
        Assert.Equal("oauthbearertoken", config.Body.GetProperty("authenticationSchemes")[0].GetProperty("type").GetString());
        Assert.Equal(["User", "Group"], types.Body.GetProperty("Resources").EnumerateArray().Select(r => r.GetProperty("id").GetString()));
        Assert.Equal(2, types.Body.GetProperty("totalResults").GetInt32());
        Assert.Equal([UserSchema, GroupSchema], schemas.Body.GetProperty("Resources").EnumerateArray().Select(r => r.GetProperty("id").GetString()));
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, "/scim/v2/Schemas/" + UserSchema, org.Token)).Status);
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, "/scim/v2/Schemas/" + GroupSchema, org.Token)).Status);
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, "/scim/v2/ResourceTypes/Group", org.Token)).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Get, "/scim/v2/ResourceTypes/Role", org.Token)).Status);
    }

    [Fact]
    public async Task OnlyAValidDirectoryTokenIsAcceptedAndEveryFailureLooksTheSame()
    {
        var org = await OrgAsync("scim-b");
        var (_, apiKey) = await Factory.CreateTenantWithKeyAsync("scim-b-key");
        var revoked = await OrgAsync("scim-b2");
        Assert.True((await InTenantAsync(revoked.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().RevokeTokenAsync(revoked.Owner, revoked.TokenId, Ct))).IsOk);
        var wrongSecret = org.Token[..^1] + (org.Token[^1] == 'A' ? 'B' : 'A');

        var failures = new List<Reply>
        {
            await SendAsync(HttpMethod.Get, Users, null),
            await SendAsync(HttpMethod.Get, Users, "scim_nonsense"),
            await SendAsync(HttpMethod.Get, Users, wrongSecret),
            await SendAsync(HttpMethod.Get, Users, revoked.Token),
            await SendAsync(HttpMethod.Get, Users, apiKey.Key), // 另一種憑證
        };

        Assert.All(failures, failure =>
        {
            Assert.Equal(HttpStatusCode.Unauthorized, failure.Status);
            Assert.Equal(ScimContentType, failure.ContentType);
            Assert.StartsWith("Bearer", failure.Challenge, StringComparison.Ordinal);
            Assert.Equal("401", failure.Body.GetProperty("status").GetString());
            Assert.Equal("urn:ietf:params:scim:api:messages:2.0:Error", failure.Body.GetProperty("schemas")[0].GetString());
        });
        Assert.Single(failures.Select(f => f.Detail).Distinct()); // 不透露是不存在、已撤銷還是密碼錯

        // 反過來也一樣：目錄同步的 token 不能呼叫 /v2。
        Assert.Equal(HttpStatusCode.Unauthorized, (await SendAsync(HttpMethod.Get, "/v2/api-keys", org.Token)).Status);
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, Users, org.Token)).Status);
    }

    [Fact]
    public async Task ATokenStopsWorkingWhenItExpires()
    {
        var org = await OrgAsync("scim-c");
        var issued = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
            .IssueTokenAsync(org.Owner, "short", Factory.Time.GetUtcNow().AddMinutes(5), Ct));
        var token = issued.Value!.Token;

        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, Users, token)).Status);
        Factory.Time.Advance(TimeSpan.FromMinutes(6));
        Assert.Equal(HttpStatusCode.Unauthorized, (await SendAsync(HttpMethod.Get, Users, token)).Status);
    }

    [Fact]
    public async Task TheSurfaceDoesNotExistUnlessTheDeploymentEnablesIt()
    {
        var org = await OrgAsync("scim-d");
        using var off = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:Enabled", "false"));

        var reply = await SendAsync(HttpMethod.Get, Users, org.Token, client: off.CreateClient());

        Assert.Equal(HttpStatusCode.NotFound, reply.Status);
    }

    [Fact]
    public async Task APushedUserIsCreatedWithTheRightShapeAndRolesInTheBodyAreIgnored()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-e");
            var email = NewEmail();

            var created = await CreateAsync(org, email, "ext-1", ""","roles":[{"value":"TenantOwner"}]""");

            Assert.Equal(HttpStatusCode.Created, created.Status);
            Assert.Equal(ScimContentType, created.ContentType);
            var id = created.Body.GetProperty("id").GetString()!;
            Assert.EndsWith($"/scim/v2/Users/{id}", created.Location, StringComparison.Ordinal);
            Assert.Equal(created.Location, created.Body.GetProperty("meta").GetProperty("location").GetString());
            Assert.Equal("User", created.Body.GetProperty("meta").GetProperty("resourceType").GetString());
            Assert.Equal(email, created.Body.GetProperty("userName").GetString());
            Assert.Equal("ext-1", created.Body.GetProperty("externalId").GetString());
            Assert.Equal("Ann Lee", created.Body.GetProperty("displayName").GetString());
            Assert.True(created.Body.GetProperty("active").GetBoolean());
            Assert.Equal(email, created.Body.GetProperty("emails")[0].GetProperty("value").GetString());
            Assert.Equal(CustomerRoles.Viewer, (await UserAsync(org, id)).Role); // 角色不經 SCIM 指定
            Assert.Single(Factory.Mail.Sent, m => m.Recipient == email);
        });
    }

    [Fact]
    public async Task CreatingTheSamePersonTwiceIsA409UniquenessErrorWithTheStableCode()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-f");
            var email = NewEmail();
            Assert.Equal(HttpStatusCode.Created, (await CreateAsync(org, email, "ext-dup")).Status);

            var sameExternalId = await CreateAsync(org, externalId: "ext-dup");
            var sameEmail = await CreateAsync(org, email);

            Assert.Equal(HttpStatusCode.Conflict, sameExternalId.Status);
            Assert.Equal("uniqueness", sameExternalId.ScimType);
            Assert.Equal("409", sameExternalId.Body.GetProperty("status").GetString());
            Assert.Equal(OutcomeCodes.DirectoryExternalIdTaken, sameExternalId.Code);
            Assert.Equal(HttpStatusCode.Conflict, sameEmail.Status);
            Assert.Equal(OutcomeCodes.MemberEmailTaken, sameEmail.Code);
        });
    }

    [Fact]
    public async Task ListingSupportsEqualityFiltersCaseInsensitivelyAndPaging()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-g");
            var emails = new[] { NewEmail(), NewEmail(), NewEmail() };
            for (var i = 0; i < emails.Length; i++)
                Assert.Equal(HttpStatusCode.Created, (await CreateAsync(org, emails[i], $"ext-{i}")).Status);

            var all = await SendAsync(HttpMethod.Get, Users, org.Token);
            var paged = await SendAsync(HttpMethod.Get, $"{Users}?startIndex=3&count=2", org.Token);
            var onlyTotal = await SendAsync(HttpMethod.Get, $"{Users}?count=0", org.Token);
            var byName = await SendAsync(HttpMethod.Get, $"{Users}?filter={Uri.EscapeDataString($"USERNAME EQ \"{emails[1].ToUpperInvariant()}\"")}", org.Token);
            var byExternal = await SendAsync(HttpMethod.Get, $"{Users}?filter={Uri.EscapeDataString("externalId eq \"ext-2\"")}", org.Token);
            var none = await SendAsync(HttpMethod.Get, $"{Users}?filter={Uri.EscapeDataString("userName eq \"nobody@example.com\"")}", org.Token);
            var unsupported = await SendAsync(HttpMethod.Get, $"{Users}?filter={Uri.EscapeDataString("userName co \"a\"")}", org.Token);

            Assert.Equal(4, all.Body.GetProperty("totalResults").GetInt32()); // 擁有者加三位
            Assert.Equal("urn:ietf:params:scim:api:messages:2.0:ListResponse", all.Body.GetProperty("schemas")[0].GetString());
            Assert.Equal(1, all.Body.GetProperty("startIndex").GetInt32());
            Assert.Equal(3, paged.Body.GetProperty("startIndex").GetInt32());
            Assert.Equal(2, paged.Body.GetProperty("itemsPerPage").GetInt32());
            Assert.Equal(2, paged.Body.GetProperty("Resources").GetArrayLength());
            Assert.Equal(0, onlyTotal.Body.GetProperty("Resources").GetArrayLength());
            Assert.Equal(4, onlyTotal.Body.GetProperty("totalResults").GetInt32());
            Assert.Equal(1, byName.Body.GetProperty("totalResults").GetInt32());
            Assert.Equal(emails[1], byName.Body.GetProperty("Resources")[0].GetProperty("userName").GetString());
            Assert.Equal(emails[2], byExternal.Body.GetProperty("Resources")[0].GetProperty("userName").GetString());
            Assert.Equal(0, none.Body.GetProperty("totalResults").GetInt32()); // 身分來源先查再決定要不要建立
            Assert.Equal(HttpStatusCode.BadRequest, unsupported.Status);
            Assert.Equal("invalidFilter", unsupported.ScimType);
        });
    }

    [Fact]
    public async Task PatchAcceptsEntraOktaAndStandardShapesAndIgnoresWhatItDoesNotKnow()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-h");
            var created = await CreateAsync(org);
            var id = created.Body.GetProperty("id").GetString()!;
            var url = $"{Users}/{id}";

            // Entra：大寫 Replace、字串 "False"。
            var entra = await SendAsync(HttpMethod.Patch, url, org.Token, Patch("""[{"op":"Replace","path":"active","value":"False"}]"""));
            Assert.Equal(HttpStatusCode.OK, entra.Status);
            Assert.False(entra.Body.GetProperty("active").GetBoolean());

            // Okta：沒有 path，值是物件；多帶的屬性（電話）略過，不使整個同步失敗。
            var okta = await SendAsync(HttpMethod.Patch, url, org.Token,
                Patch("""[{"op":"replace","value":{"active":true,"displayName":"Okta Ann","phoneNumbers":[{"value":"123"}]}}]"""));
            Assert.True(okta.Body.GetProperty("active").GetBoolean());
            Assert.Equal("Okta Ann", okta.Body.GetProperty("displayName").GetString());

            // 標準：小寫 op、布林、巢狀路徑；不認得的 path 略過。
            var standard = await SendAsync(HttpMethod.Patch, url, org.Token,
                Patch("""[{"op":"replace","path":"name.formatted","value":"Std Ann"},{"op":"replace","path":"title","value":"x"},{"op":"add","path":"externalId","value":"ext-new"}]"""));
            Assert.Equal("Std Ann", standard.Body.GetProperty("displayName").GetString());
            Assert.Equal("ext-new", standard.Body.GetProperty("externalId").GetString());

            var cleared = await SendAsync(HttpMethod.Patch, url, org.Token, Patch("""[{"op":"remove","path":"displayName"}]"""));
            Assert.False(cleared.Body.TryGetProperty("displayName", out _));
        });
    }

    [Fact]
    public async Task BadPatchesAndBodiesAreRejectedWithTheRightScimTypes()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-i");
            var id = (await CreateAsync(org)).Body.GetProperty("id").GetString()!;
            var url = $"{Users}/{id}";

            var badOp = await SendAsync(HttpMethod.Patch, url, org.Token, Patch("""[{"op":"move","path":"active","value":true}]"""));
            var noOperations = await SendAsync(HttpMethod.Patch, url, org.Token, $$"""{"schemas":["{{PatchSchema}}"]}""");
            var badActive = await SendAsync(HttpMethod.Patch, url, org.Token, Patch("""[{"op":"replace","path":"active","value":"maybe"}]"""));
            var rename = await SendAsync(HttpMethod.Patch, url, org.Token, Patch("""[{"op":"replace","path":"userName","value":"someone-else@example.com"}]"""));
            var notJson = await SendAsync(HttpMethod.Post, Users, org.Token, "{ not json");
            var noEmail = await SendAsync(HttpMethod.Post, Users, org.Token, $$"""{"schemas":["{{UserSchema}}"],"userName":"not-an-email"}""");

            Assert.Equal("invalidSyntax", badOp.ScimType);
            Assert.Equal("invalidSyntax", noOperations.ScimType);
            Assert.Equal("invalidValue", badActive.ScimType);
            Assert.Equal(HttpStatusCode.BadRequest, rename.Status);
            Assert.Equal("mutability", rename.ScimType);
            Assert.Equal(OutcomeCodes.DirectoryEmailImmutable, rename.Code);
            Assert.Equal(HttpStatusCode.BadRequest, notJson.Status);
            Assert.Equal("invalidSyntax", notJson.ScimType);
            Assert.Equal("invalidValue", noEmail.ScimType);
            Assert.Equal(OutcomeCodes.EmailInvalid, noEmail.Code);
            Assert.Equal(HttpStatusCode.BadRequest, (await SendAsync(HttpMethod.Patch, url, org.Token, "[]")).Status);
        });
    }

    [Fact]
    public async Task PutReplacesTheMutableAttributes()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-j");
            var email = NewEmail();
            var id = (await CreateAsync(org, email)).Body.GetProperty("id").GetString()!;

            var replaced = await SendAsync(HttpMethod.Put, $"{Users}/{id}", org.Token,
                $$"""{"schemas":["{{UserSchema}}"],"userName":"{{email}}","displayName":"Put Ann","active":false}""");

            Assert.Equal(HttpStatusCode.OK, replaced.Status);
            Assert.Equal("Put Ann", replaced.Body.GetProperty("displayName").GetString());
            Assert.False(replaced.Body.GetProperty("active").GetBoolean());
        });
    }

    [Fact]
    public async Task DeleteDeactivatesAndRepeatingItIsFineButAnUnknownIdIs404()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-k");
            var id = (await CreateAsync(org)).Body.GetProperty("id").GetString()!;

            var first = await SendAsync(HttpMethod.Delete, $"{Users}/{id}", org.Token);
            var again = await SendAsync(HttpMethod.Delete, $"{Users}/{id}", org.Token);
            var read = await SendAsync(HttpMethod.Get, $"{Users}/{id}", org.Token);
            var unknown = await SendAsync(HttpMethod.Delete, $"{Users}/{Guid.NewGuid()}", org.Token);

            Assert.Equal(HttpStatusCode.NoContent, first.Status);
            Assert.Equal(HttpStatusCode.NoContent, again.Status);
            Assert.Equal(HttpStatusCode.OK, read.Status); // 帳號保留，只是停用
            Assert.False(read.Body.GetProperty("active").GetBoolean());
            Assert.Equal(HttpStatusCode.NotFound, unknown.Status);
            Assert.Equal("404", unknown.Body.GetProperty("status").GetString());
            Assert.Single(await AuditAsync(org, AuditActions.MemberDeprovisioned));
        });
    }

    [Fact]
    public async Task ATokenOnlyReachesItsOwnTenantsUsers()
    {
        await Factory.WithMail(async () =>
        {
            var a = await OrgAsync("scim-l1");
            var b = await OrgAsync("scim-l2");
            var idInA = (await CreateAsync(a)).Body.GetProperty("id").GetString()!;

            var read = await SendAsync(HttpMethod.Get, $"{Users}/{idInA}", b.Token);
            var patch = await SendAsync(HttpMethod.Patch, $"{Users}/{idInA}", b.Token, Patch("""[{"op":"replace","path":"active","value":false}]"""));
            var delete = await SendAsync(HttpMethod.Delete, $"{Users}/{idInA}", b.Token);
            var listB = await SendAsync(HttpMethod.Get, Users, b.Token);

            Assert.Equal(HttpStatusCode.NotFound, read.Status);
            Assert.Equal(HttpStatusCode.NotFound, patch.Status);
            Assert.Equal(HttpStatusCode.NotFound, delete.Status);
            Assert.Equal(1, listB.Body.GetProperty("totalResults").GetInt32()); // 只有 B 自己的擁有者
            Assert.True((await SendAsync(HttpMethod.Get, $"{Users}/{idInA}", a.Token)).Body.GetProperty("active").GetBoolean());
        });
    }

    [Fact]
    public async Task ATenantThatSwitchedTheConnectionOffGetsA404AndTheOperatorCanDoItToo()
    {
        var org = await OrgAsync("scim-m");
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, Users, org.Token)).Status);

        var off = await Factory.Services.GetRequiredService<IDirectoryAdmin>().DisableAsync(OperatorSession.Principal(), org.Tenant.Id, Ct);
        var reply = await SendAsync(HttpMethod.Get, Users, org.Token);

        Assert.True(off.IsOk);
        Assert.Equal(HttpStatusCode.NotFound, reply.Status);
        Assert.Equal(OutcomeCodes.DirectoryNotEnabled, reply.Code);
        Assert.Equal(ScimContentType, reply.ContentType);
    }

    [Fact]
    public async Task ResourcesThatAreNotSupportedAnswerInScimFormatAfterAuthentication()
    {
        var org = await OrgAsync("scim-n");

        var roles = await SendAsync(HttpMethod.Get, "/scim/v2/Roles", org.Token);
        var bulk = await SendAsync(HttpMethod.Post, "/scim/v2/Bulk", org.Token, "{}");
        var anonymous = await SendAsync(HttpMethod.Get, "/scim/v2/Roles", null);

        Assert.Equal(HttpStatusCode.NotFound, roles.Status);
        Assert.Equal("404", roles.Body.GetProperty("status").GetString());
        Assert.Equal(HttpStatusCode.NotFound, bulk.Status);
        Assert.Equal(HttpStatusCode.Unauthorized, anonymous.Status); // 沒有 token 連「有沒有這個資源」都不透露
    }

    [Fact]
    public async Task TheRateLimitIsPerTokenAndRequestsWithoutAValidTokenCostNothing()
    {
        var org = await OrgAsync("scim-o");
        using var small = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:RequestsPerMinute", "3"));
        using var client = small.CreateClient();

        for (var i = 0; i < 6; i++)
            Assert.Equal(HttpStatusCode.Unauthorized, (await SendAsync(HttpMethod.Get, Users, "scim_nonsense", client: client)).Status);

        for (var i = 0; i < 3; i++)
            Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, Users, org.Token, client: client)).Status);

        var limited = await SendAsync(HttpMethod.Get, Users, org.Token, client: client);

        Assert.Equal(HttpStatusCode.TooManyRequests, limited.Status);
        Assert.Equal("429", limited.Body.GetProperty("status").GetString());
        Assert.True(int.Parse(limited.RetryAfter!, System.Globalization.CultureInfo.InvariantCulture) is > 0 and <= 60);

        // 別的租戶的 token 有自己的額度。
        var other = await OrgAsync("scim-o2");
        Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, Users, other.Token, client: client)).Status);
    }

    [Fact]
    public async Task UsingATokenRecordsWhenItWasLastUsed()
    {
        var org = await OrgAsync("scim-p");
        Assert.Null(Assert.Single((await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct))).Value!.Tokens).LastUsedAt);

        await SendAsync(HttpMethod.Get, Users, org.Token);

        Assert.NotNull(Assert.Single((await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct))).Value!.Tokens).LastUsedAt);
    }

    [Fact]
    public async Task MetricsCountOperationsAndOutcomesWithoutTenantsOrContent()
    {
        var measurements = new List<(string? Operation, string? Outcome, string[] Tags)>();
        using var listener = new MeterListener();
        listener.InstrumentPublished = (instrument, l) =>
        {
            if (instrument.Meter.Name == DirectoryMetrics.MeterName)
                l.EnableMeasurementEvents(instrument);
        };
        listener.SetMeasurementEventCallback<long>((_, _, tags, _) =>
        {
            var pairs = tags.ToArray();
            lock (measurements)
            {
                measurements.Add((
                    pairs.FirstOrDefault(t => t.Key == "operation").Value as string,
                    pairs.FirstOrDefault(t => t.Key == "outcome").Value as string,
                    [.. pairs.Select(t => t.Key)]));
            }
        });

        await Factory.WithMail(async () =>
        {
            using var host = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:MaxPageSize", "100"));
            listener.Start();
            var org = await OrgAsync("scim-q");
            using var client = host.CreateClient();
            var email = NewEmail();
            await SendAsync(HttpMethod.Post, Users, org.Token, NewUser(email), client);
            await SendAsync(HttpMethod.Get, Users, org.Token, client: client);
            await SendAsync(HttpMethod.Post, Users, org.Token, NewUser(email), client);
            await SendAsync(HttpMethod.Get, Users, null, client: client);
        });

        lock (measurements)
        {
            Assert.Contains(measurements, m => m is { Operation: "create", Outcome: "ok" });
            Assert.Contains(measurements, m => m is { Operation: "list", Outcome: "ok" });
            Assert.Contains(measurements, m => m is { Operation: "create", Outcome: "conflict" });
            Assert.Contains(measurements, m => m is { Operation: "authenticate", Outcome: "unauthorized" });
            Assert.All(measurements, m => Assert.Equal(["operation", "outcome"], m.Tags));
        }
    }

    [Fact]
    public async Task NoLogLineCarriesTheTokenTheBodyOrThePersonsEmail()
    {
        await Factory.WithMail(async () =>
        {
            var logs = new CapturingLoggerProvider();
            using var host = Factory.WithWebHostBuilder(builder => builder.ConfigureLogging(l => l.AddProvider(logs)));
            using var client = host.CreateClient();
            var org = await OrgAsync("scim-r");
            var email = NewEmail();
            Assert.True(DirectoryTokenFormat.TryParse(org.Token, out _, out var secret));

            await SendAsync(HttpMethod.Post, Users, org.Token, NewUser(email, "ext-log"), client);
            await SendAsync(HttpMethod.Post, Users, org.Token, NewUser(email, "ext-log"), client); // 衝突
            await SendAsync(HttpMethod.Get, Users, org.Token[..^2] + "xx", client: client); // 密碼錯

            var everything = string.Join('\n', logs.Messages);
            Assert.DoesNotContain(secret, everything, StringComparison.Ordinal);
            Assert.DoesNotContain(org.Token, everything, StringComparison.Ordinal);
            Assert.DoesNotContain(email, everything, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("ext-log", everything, StringComparison.Ordinal);
        });
    }

    private const string Groups = "/scim/v2/Groups";

    private async Task<string> UserIdAsync(Org org) => (await CreateAsync(org)).Body.GetProperty("id").GetString()!;

    private static string NewGroup(string name, string? externalId = null, params string[] members)
    {
        var list = string.Join(',', members.Select(m => "{\"value\":\"" + m + "\"}"));
        return "{\"schemas\":[\"" + GroupSchema + "\"],\"displayName\":\"" + name + "\",\"externalId\":\"" + (externalId ?? $"g-{Guid.NewGuid():N}") + "\",\"members\":[" + list + "]}";
    }

    [Fact]
    public async Task GroupsCanBeCreatedReadFilteredPatchedReplacedAndDeletedInTheShapesTheProvidersUse()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-s");
            var one = await UserIdAsync(org);
            var two = await UserIdAsync(org);
            var three = await UserIdAsync(org);

            var created = await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("Engineering", "ext-eng", one));
            var id = created.Body.GetProperty("id").GetString()!;

            Assert.Equal(HttpStatusCode.Created, created.Status);
            Assert.Equal(ScimContentType, created.ContentType);
            Assert.EndsWith($"/scim/v2/Groups/{id}", created.Location, StringComparison.Ordinal);
            Assert.Equal("Group", created.Body.GetProperty("meta").GetProperty("resourceType").GetString());
            var member = created.Body.GetProperty("members")[0];
            Assert.Equal(one, member.GetProperty("value").GetString());
            Assert.EndsWith($"/scim/v2/Users/{one}", member.GetProperty("$ref").GetString(), StringComparison.Ordinal);

            Assert.Equal(HttpStatusCode.OK, (await SendAsync(HttpMethod.Get, $"{Groups}/{id}", org.Token)).Status);
            var byName = await SendAsync(HttpMethod.Get, $"{Groups}?filter={Uri.EscapeDataString("displayName eq \"ENGINEERING\"")}", org.Token);
            var byExternal = await SendAsync(HttpMethod.Get, $"{Groups}?filter={Uri.EscapeDataString("externalId eq \"ext-eng\"")}", org.Token);
            var unsupported = await SendAsync(HttpMethod.Get, $"{Groups}?filter={Uri.EscapeDataString("userName eq \"x\"")}", org.Token);
            Assert.Equal(1, byName.Body.GetProperty("totalResults").GetInt32());
            Assert.Equal(id, byExternal.Body.GetProperty("Resources")[0].GetProperty("id").GetString());
            Assert.Equal("invalidFilter", unsupported.ScimType);

            // Entra：大寫 Add、用 members[value eq "…"] 移除。
            var added = await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", org.Token, Patch($$"""[{"op":"Add","path":"members","value":[{"value":"{{two}}"}]}]"""));
            Assert.Equal(2, added.Body.GetProperty("members").GetArrayLength());
            var removed = await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", org.Token, Patch($$"""[{"op":"Remove","path":"members[value eq \"{{one}}\"]"}]"""));
            Assert.Equal([two], removed.Body.GetProperty("members").EnumerateArray().Select(m => m.GetProperty("value").GetString()));

            // Okta：remove 把 Id 放在 value；沒有 path 的 replace 帶改名。
            var okta = await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", org.Token,
                Patch($$$"""[{"op":"remove","path":"members","value":[{"value":"{{{two}}}"}]},{"op":"replace","value":{"displayName":"Eng"}}]"""));
            Assert.Equal("Eng", okta.Body.GetProperty("displayName").GetString());
            Assert.Equal(0, okta.Body.GetProperty("members").GetArrayLength());

            // PUT 取代整組成員。
            var put = await SendAsync(HttpMethod.Put, $"{Groups}/{id}", org.Token, NewGroup("Eng", "ext-eng", one, three));
            Assert.Equal(2, put.Body.GetProperty("members").GetArrayLength());

            Assert.Equal(HttpStatusCode.NoContent, (await SendAsync(HttpMethod.Delete, $"{Groups}/{id}", org.Token)).Status);
            Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Delete, $"{Groups}/{id}", org.Token)).Status);
            Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Get, $"{Groups}/{id}", org.Token)).Status);
        });
    }

    [Fact]
    public async Task GroupErrorsAreScimErrorsWithTheRightTypes()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-t");
            var (manualId, _) = await AddMemberAsync(Factory, org.Tenant.Id, NewEmail(), CustomerRoles.Developer);
            Assert.Equal(HttpStatusCode.Created, (await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("Taken"))).Status);

            var duplicate = await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("taken"));
            var unmanaged = await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("Manual", null, manualId));
            var unknown = await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("Ghost", null, Guid.NewGuid().ToString()));
            var noName = await SendAsync(HttpMethod.Post, Groups, org.Token, $$"""{"schemas":["{{GroupSchema}}"]}""");
            var notJson = await SendAsync(HttpMethod.Post, Groups, org.Token, "{ nope");
            var notAGuid = await SendAsync(HttpMethod.Get, $"{Groups}/not-a-guid", org.Token);
            var badOp = await SendAsync(HttpMethod.Patch, $"{Groups}/{Guid.NewGuid()}", org.Token, Patch("""[{"op":"move","path":"members"}]"""));

            Assert.Equal(HttpStatusCode.Conflict, duplicate.Status);
            Assert.Equal("uniqueness", duplicate.ScimType);
            Assert.Equal(OutcomeCodes.DirectoryGroupNameTaken, duplicate.Code);
            Assert.Equal(HttpStatusCode.BadRequest, unmanaged.Status);
            Assert.Equal("invalidValue", unmanaged.ScimType);
            Assert.Equal(OutcomeCodes.DirectoryGroupMemberInvalid, unmanaged.Code);
            Assert.Equal(OutcomeCodes.DirectoryGroupMemberInvalid, unknown.Code);
            Assert.Equal(OutcomeCodes.DirectoryGroupInvalid, noName.Code);
            Assert.Equal("invalidSyntax", notJson.ScimType);
            Assert.Equal(HttpStatusCode.NotFound, notAGuid.Status);
            Assert.Equal("invalidSyntax", badOp.ScimType);
        });
    }

    [Fact]
    public async Task AGroupMappedToARoleDecidesItsMembersRoleAsTheProviderChangesMembership()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("scim-u");
            var member = await UserIdAsync(org);
            var group = await SendAsync(HttpMethod.Post, Groups, org.Token, NewGroup("Engineering"));
            var id = Guid.Parse(group.Body.GetProperty("id").GetString()!);
            Assert.True((await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().SetGroupRoleAsync(org.Owner, id, CustomerRoles.Developer, Ct))).IsOk);
            Assert.Equal(CustomerRoles.Viewer, (await UserAsync(org, member)).Role); // 對映只作用在成員

            await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", org.Token, Patch($$"""[{"op":"add","path":"members","value":[{"value":"{{member}}"}]}]"""));
            Assert.Equal(CustomerRoles.Developer, (await UserAsync(org, member)).Role);

            await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", org.Token, Patch($$"""[{"op":"remove","path":"members[value eq \"{{member}}\"]"}]"""));
            Assert.Equal(CustomerRoles.Viewer, (await UserAsync(org, member)).Role);
        });
    }

    [Fact]
    public async Task ATokenOnlyReachesItsOwnTenantsGroups()
    {
        await Factory.WithMail(async () =>
        {
            var a = await OrgAsync("scim-v1");
            var b = await OrgAsync("scim-v2");
            var created = await SendAsync(HttpMethod.Post, Groups, a.Token, NewGroup("Engineering"));
            var id = created.Body.GetProperty("id").GetString()!;

            Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Get, $"{Groups}/{id}", b.Token)).Status);
            Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Patch, $"{Groups}/{id}", b.Token, Patch("""[{"op":"replace","path":"displayName","value":"x"}]"""))).Status);
            Assert.Equal(HttpStatusCode.NotFound, (await SendAsync(HttpMethod.Delete, $"{Groups}/{id}", b.Token)).Status);
            Assert.Equal(0, (await SendAsync(HttpMethod.Get, Groups, b.Token)).Body.GetProperty("totalResults").GetInt32());
            Assert.Equal(HttpStatusCode.Created, (await SendAsync(HttpMethod.Post, Groups, b.Token, NewGroup("Engineering"))).Status); // 同名在另一個租戶沒事
        });
    }
}