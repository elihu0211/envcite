using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 啟用流程交給頁面外框的兩樣東西：依連結狀態決定的標題，與流程的主體（說明、錯誤、表單）。
/// 外框（標題放哪裡、要不要卡片、租戶名稱）由頁面決定，流程不管。
/// </summary>
public sealed record ActivationView(string Heading, RenderFragment Body);