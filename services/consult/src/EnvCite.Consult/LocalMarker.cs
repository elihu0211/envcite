namespace EnvCite.Consult;

/// <summary>問句有沒有指向地方自治法規的字樣。只用來量需求（見 <see cref="GroundingMetrics"/>），不影響檢索、接地與拒答。</summary>
internal enum LocalMarker
{
    None,

    /// <summary>點名了縣市，但沒有自治法規的字樣。縣市也可能只是提問者所在的地方，所以是較弱的訊號。</summary>
    Jurisdiction,

    /// <summary>出現自治法規的字樣：明白在問地方規定，是較強的訊號，所以兩類並存時取這一類。</summary>
    Ordinance,
}

/// <summary>
/// 地方標記的詞表與判定規則，只此一處。語料只含中央法規，這個判定的唯一用途是上線後計數「問了地方自治法規但語料沒有」的
/// 真實需求，作為日後是否做縣市試點的依據；詞表因此取精確而不取廣：縣市只認全名，簡稱（如「台北」）不認，寧可少計也不把
/// 無關的問句算成需求。判定結果只輸出分類值，問句與命中的詞不離開這個方法。
/// </summary>
internal static class LocalMarkers
{
    /// <summary>
    /// 22 個直轄市、縣（市）的全名，一律寫「臺」：比對前問句裡的「台」先換成「臺」，兩種寫法都認。
    /// </summary>
    public static IReadOnlyList<string> Jurisdictions { get; } =
    [
        "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
        "基隆市", "新竹市", "嘉義市",
        "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
        "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣",
    ];

    public static IReadOnlyList<string> OrdinanceTerms { get; } = ["自治條例", "自治法規", "自治規則", "地方加嚴"];

    public static LocalMarker Classify(string question)
    {
        var text = question.Replace('台', '臺');
        if (OrdinanceTerms.Any(term => text.Contains(term, StringComparison.Ordinal)))
        {
            return LocalMarker.Ordinance;
        }

        return Jurisdictions.Any(name => text.Contains(name, StringComparison.Ordinal)) ? LocalMarker.Jurisdiction : LocalMarker.None;
    }
}