using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 端對端測試的工具：<c>tests/e2e/</c> 的 playwright（npm）與它的 Chromium。第一次執行才安裝（<c>npm ci</c> 加下載瀏覽器），
/// 之後直接用；<c>ENVCITE_CHROMIUM_PATH</c> 指向現成的 Chromium／Chrome 就不下載，<c>ENVCITE_SKIP_E2E=1</c> 整組跳過（沒有網路時）。
/// CI 不設這兩個，所以一定會跑。
/// </summary>
internal static class E2EToolchain
{
    private static readonly SemaphoreSlim Gate = new(1, 1);

    private static readonly Dictionary<string, string> NoEnvironment = [];

    private static bool _ready;

    public static bool Skipped => Environment.GetEnvironmentVariable("ENVCITE_SKIP_E2E") == "1";

    public static string? Chromium => Environment.GetEnvironmentVariable("ENVCITE_CHROMIUM_PATH");

    public static string Directory([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "e2e"));

    public static async Task EnsureAsync(CancellationToken cancellationToken)
    {
        await Gate.WaitAsync(cancellationToken);
        try
        {
            if (_ready)
                return;

            var directory = Directory();
            if (!System.IO.Directory.Exists(Path.Combine(directory, "node_modules", "playwright")))
                await RunAsync("npm", ["ci", "--no-audit", "--no-fund"], NoEnvironment, TimeSpan.FromMinutes(5), cancellationToken);

            if (string.IsNullOrEmpty(Chromium))
            {
                string[] install = Environment.GetEnvironmentVariable("CI") is null
                    ? ["install", "chromium"]
                    : ["install", "--with-deps", "chromium"];
                await RunAsync("node", [Path.Combine("node_modules", "playwright", "cli.js"), .. install], NoEnvironment, TimeSpan.FromMinutes(10), cancellationToken);
            }

            _ready = true;
        }
        finally
        {
            Gate.Release();
        }
    }

    /// <summary>在 <c>tests/e2e/</c> 執行一個指令，逾時就連同子行程一起結束；結束碼不是 0 就丟出，附上輸出。</summary>
    public static async Task<string> RunAsync(
        string file, IEnumerable<string> arguments, IReadOnlyDictionary<string, string> environment, TimeSpan limit, CancellationToken cancellationToken)
    {
        var info = new ProcessStartInfo(file)
        {
            WorkingDirectory = Directory(),
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in arguments)
        {
            info.ArgumentList.Add(argument);
        }

        foreach (var (name, value) in environment)
        {
            info.Environment[name] = value;
        }

        using var process = Process.Start(info) ?? throw new InvalidOperationException($"無法啟動 {file}。");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(limit);
        var output = Task.WhenAll(process.StandardOutput.ReadToEndAsync(CancellationToken.None), process.StandardError.ReadToEndAsync(CancellationToken.None));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            process.Kill(entireProcessTree: true);
            throw new TimeoutException($"{file} {string.Join(' ', arguments)} 超過 {limit.TotalSeconds:0} 秒。輸出：{string.Concat(await output)}");
        }

        var text = string.Concat(await output);
        return process.ExitCode == 0 ? text : throw new InvalidOperationException($"{file} 結束碼 {process.ExitCode}。輸出：{text}");
    }
}