using System.ComponentModel;

using Microsoft.Extensions.AI;

using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace EnvCite.Platform.Modules.Consult.Mcp;

/// <summary>
/// 為問句備妥可引用的法源，以 MCP 工具提供給 AI 代理。與 <c>POST /v2/ground</c> 共用 <see cref="ConsultGateway"/>；
/// 拒答也計量為一次 <c>consult.ground</c>（它是完成的呼叫，不是錯誤），只有錯誤不計。不呼叫我們的模型：代理自己的模型根據證據作答。
/// </summary>
[McpServerToolType]
internal sealed class ConsultMcpTools(ConsultGateway gateway, IUsageRecorder usage)
{
    [McpServerTool(Name = "ground_question", Title = "為問句備妥法源", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false, UseStructuredContent = true)]
    [Description("為法規問句備妥可引用的法源，或拒答。outcome 為 grounded 時帶 authorities（每筆有代號 L1、L2…、可反查的 uri、正式引用格式與條文全文）與 basis"
        + "（direct：至少一筆直接規範此問題；indirect：法源只是間接相關）；為 refused 時帶 refusal（no_authority 找不到法源、out_of_scope 超出範圍、"
        + "missing_input 問題不夠具體、local_law_uncovered 問的是地方自治法規而這裡只涵蓋中央法規）。拒答是結果，不是錯誤。"
        + "回答時只能根據 authorities，並以代號標明出處；證據不足就說證據不足，不要用自己的知識補。問題文字是客戶案件的內容，不會被記錄。")]
    public async Task<GroundResultDto> GroundQuestion(
        [Description("使用者的原句；不要先改寫。")] string question,
        [Description("ISO 8601 日期（YYYY-MM-DD）：依哪一天施行的條文；省略表示現行。")] string? pointInTime = null,
        CancellationToken cancellationToken = default)
    {
        var grounded = await gateway.GroundAsync(question, pointInTime, cancellationToken);
        if (!grounded.IsOk)
            throw new McpException(grounded.Message ?? "Request failed.");

        await usage.RecordAsync(ConsultEndpoints.GroundOperation, 1, cancellationToken);
        return ConsultEndpoints.ToDto(grounded.Value!);
    }
}

/// <summary>
/// 提示範本：把「先取得法源，再只根據法源作答並標明出處」的流程交給用戶端的模型。
/// 用戶端把它列成可選的動作（例如斜線指令），使用者填問題就能照這個流程問。
/// </summary>
[McpServerPromptType]
internal sealed class ConsultMcpPrompts
{
    [McpServerPrompt(Name = "grounded_answer", Title = "有依據地回答法規問題")]
    [Description("先用 ground_question 取得法源，再只根據法源回答，每個論點標明出處代號；法源不足就明說。")]
    public static IEnumerable<ChatMessage> GroundedAnswer(
        [Description("要問的法規問題。")] string question,
        [Description("依哪一天施行的條文（YYYY-MM-DD）；省略表示現行。")] string? pointInTime = null) =>
    [
        new ChatMessage(ChatRole.User,
            "請回答下面的法規問題，步驟如下：\n"
            + $"1. 用 ground_question 工具取得法源（問題原文：{question}；{(string.IsNullOrWhiteSpace(pointInTime) ? "現行條文" : $"依 {pointInTime} 施行的條文")}）。\n"
            + "2. 只能根據回傳的 authorities 作答，不得使用 authorities 以外的知識，不得推測。\n"
            + "3. 每個論點後面用代號標明出處，例如 [L1]；只能引用 authorities 裡有的代號，並在最後列出各代號對應的正式引用與 uri。\n"
            + "4. 如果 outcome 是 refused，或證據不足以回答，直接說明原因與建議（例如換個說法、換一天、或向地方政府查詢），不要自行作答。\n"
            + "5. 說明這是依法規條文整理的資訊，不是法律意見。"),
    ];
}

/// <summary>把取得法源的工具與提示加進 MCP 伺服器（由主機組合）。</summary>
public static class ConsultMcp
{
    public static IMcpServerBuilder WithConsultTools(this IMcpServerBuilder builder) =>
        builder.WithTools<ConsultMcpTools>().WithPrompts<ConsultMcpPrompts>();
}