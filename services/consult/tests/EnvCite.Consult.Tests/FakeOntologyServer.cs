using System.Collections.Concurrent;
using System.Net;

using EnvCite.Ontology.V2;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Consult.Tests;

/// <summary>In-process h2c gRPC server standing in for envcite-ontology; answers by query and records what it was sent.</summary>
public sealed class FakeOntologyServer : IAsyncDisposable
{
    /// <summary>兩筆皆過下界的條文（0.9、0.4），第一筆達高信心。</summary>
    public const string AnswerableQuery = "露天燃燒如何處罰";

    /// <summary>條次帶「之」與語意相似度的命中，驗證轉接的對映。</summary>
    public const string MappedQuery = "排放標準";

    /// <summary>同時涵蓋中央空污法與臺北市自治條例的雙軌檢索。</summary>
    public const string DualTrackQuery = "臺北市空氣污染防制與自治裁罰標準";

    /// <summary>有命中，但都未達相關性下界。</summary>
    public const string IrrelevantQuery = "量子電腦";

    /// <summary>什麼都沒命中。</summary>
    public const string EmptyQuery = "查無此事";

    public const string InvalidQuery = "trigger-invalid";
    public const string InvalidDetail = "as_of must be an ISO 8601 date";
    public static readonly byte[] InvalidStatusDetails = [8, 3];

    public const string UnavailableQuery = "trigger-unavailable";

    private WebApplication? _app;

    public Uri Address { get; private set; } = null!;

    public ConcurrentQueue<(SearchArticlesRequest Request, DateTime Deadline)> Searches { get; } = new();

    public async Task StartAsync()
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.ConfigureKestrel(k => k.Listen(IPAddress.Loopback, 0, o => o.Protocols = HttpProtocols.Http2));
        builder.Services.AddGrpc();
        builder.Services.AddSingleton(this);
        _app = builder.Build();
        _app.MapGrpcService<Service>();
        await _app.StartAsync();
        Address = new Uri(_app.Urls.First());
    }

    public async ValueTask DisposeAsync()
    {
        if (_app is not null)
            await _app.DisposeAsync();
    }

    internal static ArticleHit Hit(int n, float lexicalScore) => new()
    {
        Iri = $"https://envcite/tw/law/O0020001/2018-08-01/zho/art_{n}",
        Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020001" },
        LawTitle = "空氣污染防制法",
        Number = n.ToString(System.Globalization.CultureInfo.InvariantCulture),
        Content = $"第 {n} 條條文內容。",
        LexicalScore = lexicalScore,
    };

    private sealed class Service(FakeOntologyServer server) : OntologyService.OntologyServiceBase
    {
        public override Task<SearchArticlesResponse> SearchArticles(SearchArticlesRequest request, ServerCallContext context)
        {
            server.Searches.Enqueue((request, context.Deadline));
            return request.Query switch
            {
                AnswerableQuery => Respond(Hit(1, 9F), Hit(2, 4.5F)),
                DualTrackQuery => Respond(
                    new ArticleHit
                    {
                        Iri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_20",
                        Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020001" },
                        LawTitle = "空氣污染防制法",
                        Number = "20",
                        Content = "公私場所固定污染源排放空氣污染物，應符合排放標準。",
                        LexicalScore = 8.5F,
                        SemanticSimilarity = 0.9F,
                    },
                    new ArticleHit
                    {
                        Iri = "https://envcite/tw/law/TPE-ENV-001/2023-01-01/zho/art_5",
                        Identifier = new LawIdentifier { Jurisdiction = "TW-TPE", IdScheme = "local_glrs", LocalId = "TPE-ENV-001" },
                        LawTitle = "臺北市宜居城市環境保護自治條例",
                        Number = "5",
                        Content = "臺北市轄內各公私場所應遵行嚴於中央法規之特定區域排放標準；違反者得加重裁處。",
                        LexicalScore = 7.8F,
                        SemanticSimilarity = 0.88F,
                    }),
                MappedQuery => Respond(
                    new ArticleHit { Iri = "a", Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020001" }, LawTitle = "空氣污染防制法", Number = "5-1", Content = "…", LexicalScore = 4.5F, SemanticSimilarity = 0.85F },
                    new ArticleHit { Iri = "b", Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020001" }, LawTitle = "空氣污染防制法", Number = "20", Content = "…", LexicalScore = 0.1F }),
                IrrelevantQuery => Respond(Hit(1, 0.1F)),
                InvalidQuery => throw new RpcException(
                    new Status(StatusCode.InvalidArgument, InvalidDetail),
                    new Metadata { { "grpc-status-details-bin", InvalidStatusDetails } }),
                UnavailableQuery => throw new RpcException(new Status(StatusCode.Unavailable, "restarting")),
                _ => Respond(),
            };
        }

        private static Task<SearchArticlesResponse> Respond(params ArticleHit[] hits) =>
            Task.FromResult(new SearchArticlesResponse { Hits = { hits } });
    }
}