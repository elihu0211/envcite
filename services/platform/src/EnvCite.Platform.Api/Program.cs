using EnvCite.Platform.Api;
using EnvCite.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.Configuration.AddServiceEnvironment().AddRelevanceFloors();

// 預覽信件版型不寄信也不連資料庫，在建立主機（遷移、種子資料）之前就結束。
if (MailPreview.IsCommand(args))
    return await MailPreview.RunAsync(builder.Configuration, args, Console.Out, Console.Error, CancellationToken.None);

var generatingOpenApi = PlatformHost.IsGeneratingOpenApi;
if (generatingOpenApi)
    builder.Configuration.AddInMemoryCollection(PlatformHost.OpenApiPlaceholders);

builder.AddPlatform(generatingOpenApi);

var app = builder.Build();

var hostOptions = app.Services.GetRequiredService<IOptions<PlatformHostOptions>>().Value;

// 產生 OpenAPI 規格的模擬伺服器沒有資料庫，不能遷移（Development 設定開著時也一樣）。
if (!generatingOpenApi && hostOptions.ApplyMigrationsOnStartup)
    await PlatformStartup.MigrateAsync(app);

// 只在 Development 且設定開著時建立。
if (!generatingOpenApi && app.Environment.IsDevelopment() && hostOptions.SeedDevelopmentData)
    await PlatformStartup.SeedDevelopmentDataAsync(app);

// 營運者命令列（建立、救援）要用資料庫，所以在遷移之後；跑完就結束，不啟動 HTTP 伺服器。
if (!generatingOpenApi && OperatorCli.IsCommand(args))
    return await OperatorCli.RunAsync(app.Services, args, Console.Out, Console.Error, CancellationToken.None);

app.UsePlatformPipeline();
app.MapPlatformEndpoints();

await app.RunAsync();
return 0;