using System.Collections.Concurrent;
using System.Net;

using EnvLaw.Ontology.V1;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Tests;

/// <summary>In-process h2c gRPC server standing in for env-law-ontology; records call metadata.</summary>
public sealed class FakeOntologyServer : IAsyncDisposable
{
    public const string InternalToken = "test-ontology-token";
    public const string KnownLawId = "O0020001";
    public const string UnavailableLawId = "trigger-unavailable";

    private WebApplication? _app;

    public Uri Address { get; private set; } = null!;

    public ConcurrentQueue<Metadata> ReceivedMetadata { get; } = new();

    /// <summary>最近一次 SearchArticles 收到的請求（測試用，驗證參數有原樣傳到內部服務）。</summary>
    public SearchArticlesRequest? LastSearch { get; private set; }

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
        {
            await _app.DisposeAsync();
        }
    }

    private sealed class Service(FakeOntologyServer server) : OntologyService.OntologyServiceBase
    {
        public override Task<GetLawResponse> GetLaw(GetLawRequest request, ServerCallContext context)
        {
            Authorize(context, request.Id);
            return Task.FromResult(new GetLawResponse
            {
                Law = new Law
                {
                    Id = request.Id,
                    Iri = $"https://env.law/eli/tw/law/{request.Id}",
                    Title = "空氣污染防制法",
                    Language = "zh-hant",
                    DateDocument = "2000-01-01",
                },
            });
        }

        public override Task<ListArticlesResponse> ListArticles(ListArticlesRequest request, ServerCallContext context)
        {
            Authorize(context, request.LawId);
            return Task.FromResult(new ListArticlesResponse
            {
                Articles = { new Article { Iri = "a1", Number = "1" }, new Article { Iri = "a5-1", Number = "5-1" } },
            });
        }

        public override Task<SearchArticlesResponse> SearchArticles(SearchArticlesRequest request, ServerCallContext context)
        {
            Authorize(context, KnownLawId);
            server.LastSearch = request;
            if (string.IsNullOrWhiteSpace(request.Query))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "query must not be empty"));
            }

            if (request.AsOf == "yesterday")
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "as_of must be an ISO 8601 date"));
            }

            return Task.FromResult(new SearchArticlesResponse
            {
                Hits =
                {
                    new ArticleHit
                    {
                        Iri = "a5-1", LawId = KnownLawId, LawTitle = "空氣污染防制法", Number = "5-1",
                        Content = "（條文內容）", LexicalScore = 0.42f,
                    },
                },
            });
        }

        private void Authorize(ServerCallContext context, string lawId)
        {
            server.ReceivedMetadata.Enqueue(context.RequestHeaders);
            if (context.RequestHeaders.GetValue("authorization") != $"Bearer {InternalToken}")
            {
                throw new RpcException(new Status(StatusCode.Unauthenticated, "bad token"));
            }

            if (lawId == UnavailableLawId)
            {
                throw new RpcException(new Status(StatusCode.Unavailable, "down"));
            }

            if (lawId != KnownLawId)
            {
                throw new RpcException(new Status(StatusCode.NotFound, "no such law"));
            }
        }
    }
}