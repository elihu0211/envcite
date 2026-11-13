using System.ComponentModel.DataAnnotations;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.Memory;
using Microsoft.Extensions.Options;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// 檢索結果的相關性下界（<c>Relevance</c> 區段）：字面分數或語意相似度任一達標才算相關。
/// 下界量的是同一個檢索，所以所有讀它的服務（consult 的證據集、platform 的搜尋頁與 <c>/v2/search</c>）必須是同一組數字；
/// 值只寫在這個專案的 <c>relevance-floors.json</c> 一處，以內嵌資源隨組件帶進各服務，不在各服務的設定檔重複。
/// 它們是受模型制約的值，不是領域常數：只在產生它的評分方式（嵌入模型、分詞器、檢索範圍）下才可比較，
/// 評分方式一改就必須以同一批問句重新量測後重訂。
/// </summary>
public sealed class RelevanceFloorsOptions : ISectionOptions
{
    public static string SectionName => "Relevance";

    /// <summary>字面臂分數的下界。</summary>
    [Range(0.0001, 100)]
    public double LexicalFloor { get; set; }

    /// <summary>語意臂餘弦相似度的下界。</summary>
    [Range(0.0001, 0.9999)]
    public double SemanticFloor { get; set; }
}

[OptionsValidator]
public sealed partial class RelevanceFloorsOptionsValidator : IValidateOptions<RelevanceFloorsOptions>;

public static class RelevanceFloors
{
    private const string ResourceName = "relevance-floors.json";

    /// <summary>
    /// 把共用的下界加成<b>優先序最低</b>的設定來源：appsettings、環境變數與命令列都能覆寫它，
    /// 覆寫值同樣在啟動時驗證。位置在最前面，所以呼叫順序不影響別的來源。
    /// 以記憶體來源而不是檔案或串流來源：來源被移除或重建時（如 <see cref="ServiceEnvironment"/>）不會重讀已耗盡的串流。
    /// </summary>
    public static ConfigurationManager AddRelevanceFloors(this ConfigurationManager configuration)
    {
        var assembly = typeof(RelevanceFloors).Assembly;
        var name = assembly.GetManifestResourceNames().Single(n => n.EndsWith(ResourceName, StringComparison.Ordinal));
        using var stream = assembly.GetManifestResourceStream(name)
            ?? throw new InvalidOperationException($"Embedded resource {name} is missing.");

        var shared = new ConfigurationBuilder().AddJsonStream(stream).Build();
        var data = shared.AsEnumerable().Where(pair => pair.Value is not null).ToDictionary(pair => pair.Key, pair => pair.Value);
        configuration.Sources.Insert(0, new MemoryConfigurationSource { InitialData = data });
        return configuration;
    }
}