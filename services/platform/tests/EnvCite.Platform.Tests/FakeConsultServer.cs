using System.Collections.Concurrent;
using System.Net;

using EnvCite.Consult.V2;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Server.Kestrel.Core;

namespace EnvCite.Platform.Tests;

/// <summary>In-process h2c gRPC server standing in for envcite-consult; records call metadata.</summary>
public sealed class FakeConsultServer : IAsyncDisposable
{
    public const string AnswerableQuestion = "露天燃燒如何處罰";
    public const string UnrelatedQuestion = "量子電腦";
    public const string UnavailableQuestion = "trigger-unavailable";
    public const string LocalLawQuestion = "臺北市有哪些空氣污染自治條例";

    private WebApplication? _app;

    public Uri Address { get; private set; } = null!;

    public ConcurrentQueue<(GroundRequest Request, Metadata Headers)> Received { get; } = new();

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

    private sealed class Service(FakeConsultServer server) : ConsultationService.ConsultationServiceBase
    {
        public override Task<GroundResponse> Ground(GroundRequest request, ServerCallContext context)
        {
            server.Received.Enqueue((request, context.RequestHeaders));
            if (request.AsOf is { Length: > 0 } && !DateOnly.TryParseExact(request.AsOf, "yyyy-MM-dd", out _))
                throw new RpcException(new Status(StatusCode.InvalidArgument, "as_of must be an ISO 8601 date"));

            return request.Question switch
            {
                UnavailableQuestion => throw new RpcException(new Status(StatusCode.Unavailable, "down")),
                LocalLawQuestion => Task.FromResult(new GroundResponse
                {
                    Refused = new Refused { Reason = Refused.Types.Reason.LocalLawUncovered, Detail = "問句指向地方自治法規，語料只涵蓋中央法規" },
                }),
                AnswerableQuestion => Task.FromResult(new GroundResponse
                {
                    Evidence = new Evidence
                    {
                        Citations =
                        {
                            new Citation
                            {
                                Code = "L1",
                                Iri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32",
                                Label = "空氣污染防制法 第 32 條",
                                Text = "在指定清除地區內，不得從事燃燒行為。",
                            },
                        },
                        Confidence = Confidence.High,
                    },
                }),
                _ => Task.FromResult(new GroundResponse
                {
                    Refused = new Refused { Reason = Refused.Types.Reason.OutOfScope, Detail = "檢索結果皆未達相關性下界" },
                }),
            };
        }
    }
}