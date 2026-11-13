using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Ontology;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 「只打了主題詞、沒有問句」的判準。「是不是問句」讀搜尋端的同一份形態判準（<see cref="LawSearchOptions.Shape"/>），
/// 再加沒有句末標點、沒有疑問詞兩道只有入口需要的條件。判錯的兩個方向代價不同——誤命中換掉使用者的問題，
/// 漏命中有降級兜底——所以邊界案例要釘死。
/// </summary>
public sealed class BareTopicTests
{
    [Theory]
    [InlineData("露天燃燒")]
    [InlineData("  露天燃燒  ")]
    [InlineData("噪音")]
    [InlineData("噪音 罰則")] // 入口的說明教使用者用空白分隔關鍵字
    [InlineData("一二三四五六七八九")] // 9 個字：搜尋端的問句切點是 10，差一個字
    [InlineData("水污染防治法施行細則")] // 法規名稱不論多長都不是問句
    public void AKeywordOrANameWithoutAQuestionIsABareTopic(string text) => Assert.True(BareTopic.Is(text));

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("一二三四五六七八九十")] // 10 個字：搜尋端當問句
    [InlineData("未經許可設置固定污染源")] // 11 個字：搜尋端當問句，所以入口不能當主題詞（兩邊曾經矛盾）
    [InlineData("露天燃燒如何處罰")] // 疑問詞
    [InlineData("露天燃燒會罰嗎")]
    [InlineData("露天燃燒罰多少")]
    [InlineData("是否可以露天燃燒")]
    [InlineData("露天燃燒。")] // 句末標點（全形與半形）
    [InlineData("露天燃燒?")]
    [InlineData("露天燃燒？")]
    [InlineData("水污染防治法？")] // 法規名稱加問號：搜尋端是名稱，入口仍有句末標點
    [InlineData("a;b")]
    public void AQuestionOrASentenceIsNot(string? text) => Assert.False(BareTopic.Is(text));

    // 不變式：主題詞一定不是搜尋端的問句。同一個輸入不能在搜尋端用問句的下界、在入口卻被當成沒有問句。
    [Theory]
    [InlineData("露天燃燒")]
    [InlineData("一二三四五六七八九")]
    [InlineData("一二三四五六七八九十")]
    [InlineData("未經許可設置固定污染源")]
    [InlineData("空氣污染防制法施行細則")]
    [InlineData("露天燃燒的規定有哪些")]
    [InlineData("噪音？")]
    public void ABareTopicIsNeverAQuestionToTheSearchSide(string text)
    {
        if (BareTopic.Is(text))
            Assert.NotEqual(QueryKind.Question, LawSearchOptions.Shape(text));
    }
}