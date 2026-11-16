namespace EnvCite.Platform.Web.Components.Portal;

/// <summary>法規樹與圖譜節點的純運算：節點鍵、關鍵字過濾、焦點所在的分支。不碰元件與服務。</summary>
internal static class LawTree
{
    /// <summary>節點在樹與圖譜裡的鍵（管轄、編碼方案、本地代號三者合起來才唯一）。</summary>
    public static string Key(string jurisdiction, string idScheme, string localId) => $"{jurisdiction}|{idScheme}|{localId}";

    private static string Key(PortalLawTreeNode node) => Key(node.Jurisdiction, node.IdScheme, node.LocalId);

    /// <summary>
    /// 以名稱或代號過濾，保留命中者與它的祖先。自己命中而子孫都沒命中時留下完整的子樹
    /// （搜到母法就該看到它底下的子法），子孫命中時只留命中的分支。
    /// </summary>
    public static IReadOnlyList<PortalLawTreeNode> Filter(IReadOnlyList<PortalLawTreeNode> nodes, string? query)
    {
        if (string.IsNullOrWhiteSpace(query))
            return nodes;

        var term = query.Trim();
        var matched = new List<PortalLawTreeNode>();
        foreach (var node in nodes)
        {
            var filteredChildren = Filter(node.Children, term);
            var selfMatches = node.Title.Contains(term, StringComparison.OrdinalIgnoreCase)
                || node.LocalId.Contains(term, StringComparison.OrdinalIgnoreCase);
            if (selfMatches || filteredChildren.Count > 0)
                matched.Add(node with { Children = selfMatches && filteredChildren.Count == 0 ? node.Children : filteredChildren });
        }

        return matched;
    }

    /// <summary>從根到目標節點路徑上所有節點的鍵（含目標自己）；樹裡沒有目標則為空。頁面據此預設展開焦點所在的分支。</summary>
    public static HashSet<string> ActiveBranch(IReadOnlyList<PortalLawTreeNode> roots, string targetKey)
    {
        var keys = new HashSet<string>(StringComparer.Ordinal);

        bool Walk(PortalLawTreeNode node)
        {
            var onPath = Key(node) == targetKey;
            foreach (var child in node.Children)
                onPath |= Walk(child);

            if (onPath)
                keys.Add(Key(node));

            return onPath;
        }

        foreach (var root in roots)
            Walk(root);

        return keys;
    }

    /// <summary>圖譜裡焦點所對應的節點；沒有焦點或節點不在圖譜內為 null。</summary>
    public static PortalLawGraphNode? FindFocus(PortalLawGraphResult graph) =>
        graph.Focus is null
            ? null
            : graph.Nodes.FirstOrDefault(n => n.Key == graph.Focus);
}