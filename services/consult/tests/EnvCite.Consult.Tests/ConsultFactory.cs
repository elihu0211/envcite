using EnvCite.Consult.V2;

using Grpc.Core;
using Grpc.Net.Client;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Consult.Tests;

/// <summary>Boots consult in memory against an in-process fake ontology.</summary>
public sealed class ConsultFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    public static readonly Metadata CallMetadata = new() { { "x-tenant-id", "acme" }, { "x-request-id", "req-1" } };

    public FakeOntologyServer Ontology { get; } = new();

    public ValueTask InitializeAsync() => new(Ontology.StartAsync());

    public override async ValueTask DisposeAsync()
    {
        await base.DisposeAsync();
        await Ontology.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder) =>
        builder.UseSetting("Ontology:Address", Ontology.Address.ToString());

    /// <summary>gRPC channel over the in-memory test server.</summary>
    public static GrpcChannel ChannelFor(WebApplicationFactory<Program> host) =>
        GrpcChannel.ForAddress(host.Server.BaseAddress, new GrpcChannelOptions
        {
            HttpHandler = new ResponseVersionHandler { InnerHandler = host.Server.CreateHandler() },
        });

    public ConsultationService.ConsultationServiceClient CreateClient(WebApplicationFactory<Program>? host = null) =>
        new(ChannelFor(host ?? this));

    /// <summary>The test server answers HTTP/1.1; the gRPC client requires the response to be HTTP/2.</summary>
    private sealed class ResponseVersionHandler : DelegatingHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var response = await base.SendAsync(request, cancellationToken);
            response.Version = request.Version;
            return response;
        }
    }
}

[CollectionDefinition(Name)]
public sealed class ConsultTestGroup : ICollectionFixture<ConsultFactory>
{
    public const string Name = "consult";
}