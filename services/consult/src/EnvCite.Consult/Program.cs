using EnvCite.Consult;
using EnvCite.Ontology.V1;
using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Server.Kestrel.Core;

using OpenTelemetry.Metrics;

var builder = WebApplication.CreateBuilder(args);

// 叢集內是明文 h2c：沒有 TLS 就沒有 ALPN 可協商，端點只能是 HTTP/2。埠由 ASPNETCORE_HTTP_PORTS 給（映像內固定）。
builder.WebHost.ConfigureKestrel(kestrel =>
    kestrel.ConfigureEndpointDefaults(endpoint => endpoint.Protocols = HttpProtocols.Http2));
// 低於 k8s 的 terminationGracePeriodSeconds（30）：排空與進行中的呼叫在 kubelet 的 SIGKILL 之前完成。
builder.Services.Configure<HostOptions>(host => host.ShutdownTimeout = TimeSpan.FromSeconds(25));
builder.Services.AddSingleton(TimeProvider.System);

builder.AddServiceTelemetry().WithMetrics(metrics => metrics.AddMeter(GrpcServerMetrics.MeterName));

builder.Services.AddSingleton<GrpcServerMetrics>();
builder.Services.AddGrpc().AddServiceOptions<ConsultationGrpcService>(o => o.Interceptors.Add<GrpcServerMetrics>());

// 呼叫端的期限與取消隨 call context 帶到本體，與自己的上限取小者（見 OntologySearch）。
builder.Services.AddInternalGrpcClient<OntologyService.OntologyServiceClient, OntologyOptions>()
    .EnableCallContextPropagation();
builder.Services.AddScoped<IOntologySearch, OntologySearch>();
builder.Services.AddScoped<GroundingPipeline>();

// 空字串（liveness）與服務名稱（readiness）都回報排空狀態；本服務不查依賴，本體掛了重啟這裡沒有用。
builder.Services.AddSingleton<Draining>();
builder.Services.AddHostedService(provider => provider.GetRequiredService<Draining>());
builder.Services.AddGrpcHealthChecks(o => o.Services.Map(ConsultationGrpcService.ServiceName, _ => true))
    .AddCheck<Draining>("draining");

var app = builder.Build();

app.MapGrpcService<ConsultationGrpcService>();
// 探針每幾秒打一次：不進請求指標（追蹤也排除，見 ServiceDefaults 的 Telemetry）。
app.MapGrpcHealthChecksService().DisableHttpMetrics();

await app.RunAsync();