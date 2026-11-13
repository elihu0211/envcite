using System.Globalization;

using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Tests;

/// <summary>假的在地化：回傳鍵本身（中文原文），帶參數的鍵照 <c>{0}</c> 格式化。測的是「說了什麼」，不是某個語言的翻譯。</summary>
internal sealed class KeyLocalizer : IStringLocalizer<Ui>
{
    public LocalizedString this[string name] => new(name, name);

    public LocalizedString this[string name, params object[] arguments] =>
        new(name, string.Format(CultureInfo.InvariantCulture, name, arguments));

    public IEnumerable<LocalizedString> GetAllStrings(bool includeParentCultures) => [];
}