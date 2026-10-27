using System.Globalization;
using System.Runtime.CompilerServices;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 測試的介面語言固定為預設的繁體中文，不看執行測試的機器的系統語言（英文系統上標籤會變成英文）。
/// 要測英文的測試自己用 <see cref="Use"/> 切，離開範圍就還原。
/// </summary>
internal static class TestCulture
{
    [ModuleInitializer]
    internal static void Initialize()
    {
        var culture = new CultureInfo("zh-TW");
        CultureInfo.DefaultThreadCurrentCulture = culture;
        CultureInfo.DefaultThreadCurrentUICulture = culture;
        CultureInfo.CurrentCulture = culture;
        CultureInfo.CurrentUICulture = culture;
    }

    public static IDisposable Use(string name) => new Scope(name);

    private sealed class Scope : IDisposable
    {
        private readonly CultureInfo _previous = CultureInfo.CurrentUICulture;

        public Scope(string name) => CultureInfo.CurrentUICulture = new CultureInfo(name);

        public void Dispose() => CultureInfo.CurrentUICulture = _previous;
    }
}