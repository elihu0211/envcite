namespace EnvCite.Platform.Modules.Canon;

/// <summary>
/// 法規目錄的形狀：扁平的法規清單收成樹，編章節與條文收成結構。只看已取回的資料，不查詢、不快取。
/// </summary>
internal static class LawCatalog
{
    /// <summary>
    /// 以母法識別碼串成樹。母法不在這份清單裡（或沒有母法）的就是根；順序沿用清單的順序。
    /// </summary>
    public static IReadOnlyList<PortalLawTreeNode> Tree(IReadOnlyList<LawSummaryDto> laws)
    {
        static string KeyOf(LawIdentifierDto id) => $"{id.Jurisdiction}:{id.IdScheme}:{id.LocalId}";

        var childrenByParent = new Dictionary<string, List<LawSummaryDto>>(StringComparer.Ordinal);
        var roots = new List<LawSummaryDto>();
        var knownKeys = new HashSet<string>(laws.Select(l => KeyOf(l.Identifier)), StringComparer.Ordinal);

        foreach (var law in laws)
        {
            if (law.ParentIdentifier is { } parent && knownKeys.Contains(KeyOf(parent)))
            {
                var parentKey = KeyOf(parent);
                if (!childrenByParent.TryGetValue(parentKey, out var bucket))
                {
                    bucket = [];
                    childrenByParent[parentKey] = bucket;
                }

                bucket.Add(law);
            }
            else
            {
                roots.Add(law);
            }
        }

        PortalLawTreeNode ToNode(LawSummaryDto item)
        {
            var kids = childrenByParent.TryGetValue(KeyOf(item.Identifier), out var list)
                ? list.Select(ToNode).ToList()
                : [];
            return new PortalLawTreeNode(
                item.Identifier.Jurisdiction,
                item.Identifier.IdScheme,
                item.Identifier.LocalId,
                item.Title,
                item.NormKind,
                item.DateDocument,
                kids,
                item.DocumentType,
                item.ChildCount,
                item.ParentCount,
                item.GuidanceCount);
        }

        return [.. roots.Select(ToNode)];
    }

    /// <summary>
    /// 編章節依 <c>ParentIndex</c> 成樹，條文歸到排序鍵不大於它、且最靠近的那個編章節。
    /// 沒有編章節時不把條文鋪成一層（頁面改列扁平的條文表）。指回自己的編章節當成根；走回頭的環只展開一次。
    /// </summary>
    public static IReadOnlyList<PortalStructureNode> Structure(
        IReadOnlyList<StructureHeadingDto> headings,
        IReadOnlyList<ArticleWithSortDto> articles)
    {
        if (headings.Count == 0)
            return [];

        var childHeadingIndices = Enumerable.Range(0, headings.Count).Select(_ => new List<int>()).ToArray();
        var rootIndices = new List<int>();
        for (var i = 0; i < headings.Count; i++)
        {
            var parent = headings[i].ParentIndex;
            if (parent >= 0 && parent < headings.Count && parent != i)
                childHeadingIndices[parent].Add(i);
            else
                rootIndices.Add(i);
        }

        var ownedArticles = Enumerable.Range(0, headings.Count).Select(_ => new List<PortalLawArticle>()).ToArray();
        var headingIndex = 0;
        foreach (var article in articles)
        {
            while (headingIndex + 1 < headings.Count && CompareSortKeys(headings[headingIndex + 1].SortKey, article.SortKey) <= 0)
                headingIndex++;
            if (CompareSortKeys(headings[headingIndex].SortKey, article.SortKey) <= 0)
                ownedArticles[headingIndex].Add(new PortalLawArticle(article.Uri, article.Number));
        }

        var visited = new HashSet<int>();
        PortalStructureNode ToNode(int index)
        {
            var heading = headings[index];
            if (!visited.Add(index))
                return new PortalStructureNode(heading.Kind, heading.HeadingNo, heading.Title, [], []);
            var children = childHeadingIndices[index].Select(ToNode).ToList();
            return new PortalStructureNode(heading.Kind, heading.HeadingNo, heading.Title, children, ownedArticles[index]);
        }

        return [.. rootIndices.Select(ToNode)];
    }

    private static int CompareSortKeys(IReadOnlyList<int> left, IReadOnlyList<int> right)
    {
        var length = Math.Min(left.Count, right.Count);
        for (var i = 0; i < length; i++)
        {
            var compared = left[i].CompareTo(right[i]);
            if (compared != 0)
                return compared;
        }

        return left.Count.CompareTo(right.Count);
    }
}