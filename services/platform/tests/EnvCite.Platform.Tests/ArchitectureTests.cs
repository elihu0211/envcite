using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using System.Xml.Linq;

using Microsoft.AspNetCore.TestHost;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 模組化與依賴注入的規矩，由機器守住而不是靠自律（規矩與理由見 <c>services/platform/AGENTS.md</c>）：
/// 模組只參考共用核心與資料層、不用服務定位器、元件不碰 HttpContext、不註冊可處置的 transient。
/// 掃的是原始碼與專案檔，所以違反的人在 CI 看得到是哪個檔案。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed partial class ArchitectureTests(PlatformFactory factory)
{
    private static string Platform([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", ".."));

    private static string Src => Path.Combine(Platform(), "src");

    private static IEnumerable<string> Sources(string project, params string[] extensions) =>
        Directory.EnumerateFiles(Path.Combine(Src, project), "*.*", SearchOption.AllDirectories)
            .Where(f => extensions.Any(f.EndsWith))
            .Where(f => !f.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                && !f.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                && !f.Contains($"{Path.DirectorySeparatorChar}Migrations{Path.DirectorySeparatorChar}", StringComparison.Ordinal));

    private static string Relative(string file) => Path.GetRelativePath(Src, file).Replace('\\', '/');

    /// <summary>平台自己的專案之間的參考；倉庫共用的 <c>libs/dotnet</c> 不在這些規矩的範圍內。</summary>
    private static Dictionary<string, string[]> References() =>
        Directory.EnumerateFiles(Src, "*.csproj", SearchOption.AllDirectories)
            .ToDictionary(
                f => Path.GetFileNameWithoutExtension(f),
                f => XDocument.Load(f).Descendants("ProjectReference")
                    .Select(r => Path.GetFileNameWithoutExtension(((string)r.Attribute("Include")!).Replace('\\', '/')))
                    .Where(name => name.StartsWith("EnvCite.Platform.", StringComparison.Ordinal))
                    .Order(StringComparer.Ordinal)
                    .ToArray());

    // 模組是專案：只參考 SharedKernel 與 Data，彼此不直接參考，跨模組契約放 SharedKernel。
    // 唯一的例外：consult.proto 匯入 ontology.proto，產生的型別在 Ontology 模組裡。
    [Fact]
    public void ModulesReferenceOnlyTheSharedKernelAndTheDataLayer()
    {
        var allowedExceptions = new Dictionary<string, string[]>
        {
            ["EnvCite.Platform.Modules.Consult"] = ["EnvCite.Platform.Modules.Ontology"],
        };
        var violations = References()
            .Where(p => p.Key.StartsWith("EnvCite.Platform.Modules.", StringComparison.Ordinal))
            .SelectMany(p => p.Value
                .Where(r => r is not "EnvCite.Platform.SharedKernel" and not "EnvCite.Platform.Data")
                .Where(r => !(allowedExceptions.TryGetValue(p.Key, out var allowed) && allowed.Contains(r)))
                .Select(r => $"{p.Key} → {r}"))
            .ToList();

        Assert.Empty(violations);
    }

    // 依賴只往核心：核心不參考任何人，資料層只參考核心，介面（Web）只參考核心——不經模組也不碰資料層。
    [Fact]
    public void TheCoreReferencesNothingTheDataLayerOnlyTheCoreAndTheWebOnlyTheCore()
    {
        var references = References();

        Assert.Empty(references["EnvCite.Platform.SharedKernel"]);
        Assert.Equal(["EnvCite.Platform.SharedKernel"], references["EnvCite.Platform.Data"]);
        Assert.Equal(["EnvCite.Platform.SharedKernel"], references["EnvCite.Platform.Web"]);
    }

    // 服務定位器（注入 IServiceProvider 再自己解析）把依賴藏起來：看建構式看不出它要什麼，也測不到。
    // 只有組合根（主機、命令列、種子資料、Web 的註冊）與 IServiceProvider 自己的實作可以碰它。
    [Fact]
    public void NoServiceDependsOnTheServiceProviderOutsideTheCompositionRoots()
    {
        string[] compositionRoots =
        [
            "EnvCite.Platform.Api/",
            "EnvCite.Platform.Web/WebModule.cs",
            "EnvCite.Platform.SharedKernel/MailComposer.cs",
        ];

        var violations = Directory.EnumerateDirectories(Src)
            .SelectMany(project => Sources(Path.GetFileName(project), ".cs", ".razor"))
            .Where(f => !compositionRoots.Any(root => Relative(f).StartsWith(root, StringComparison.Ordinal)))
            .Where(f => ServiceProviderToken().IsMatch(File.ReadAllText(f)))
            .Select(Relative)
            .ToList();

        Assert.Empty(violations);
    }

    // 互動式元件沒有（可靠的）HttpContext：身分用 AuthenticationState，租戶由路由參數帶入。
    [Fact]
    public void ComponentsNeverUseTheHttpContextAccessor()
    {
        var violations = Sources("EnvCite.Platform.Web", ".cs", ".razor")
            .Where(f => File.ReadAllText(f).Contains("IHttpContextAccessor", StringComparison.Ordinal))
            .Select(Relative)
            .ToList();

        Assert.Empty(violations);
    }

    // 可處置的 transient 會被容器抓到 circuit／應用程式結束才放掉（記憶體洩漏）：我們自己的型別完全不註冊。
    // 框架與函式庫的（例如 HttpClient 的處理常式）不在範圍內。
    [Fact]
    public void NoDisposableTransientOfOursIsRegistered()
    {
        IReadOnlyList<ServiceDescriptor> registered = [];
        using var host = factory.WithWebHostBuilder(b => b.ConfigureTestServices(services => registered = [.. services]));
        _ = host.Services;

        var disposableTransients = registered
            .Where(d => d.Lifetime == ServiceLifetime.Transient)
            .Select(d => d.ImplementationType)
            .OfType<Type>()
            .Where(t => t.Assembly.GetName().Name!.StartsWith("EnvCite.", StringComparison.Ordinal))
            .Where(t => t.IsAssignableTo(typeof(IDisposable)) || t.IsAssignableTo(typeof(IAsyncDisposable)))
            .Select(t => t.FullName)
            .ToList();

        Assert.NotEmpty(registered);
        Assert.Empty(disposableTransients);
    }

    [GeneratedRegex(@"\bIServiceProvider\b", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex ServiceProviderToken();
}