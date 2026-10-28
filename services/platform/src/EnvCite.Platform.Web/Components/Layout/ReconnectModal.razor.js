// 與 .NET 範本的 ReconnectModal.razor.js 相同的狀態處理，另外不讓 Esc 關掉提示：連線斷掉時畫面已不能操作，
// 關掉提示只會留下一個看似正常、實際沒有反應的頁面。瀏覽器在使用者沒有互動過時不理會 cancel 的 preventDefault，
// 所以還要在被關掉、而連線仍未恢復時重新打開。
const reconnectModal = document.getElementById("components-reconnect-modal");
let connected = true;
reconnectModal.addEventListener("components-reconnect-state-changed", handleReconnectStateChanged);
reconnectModal.addEventListener("cancel", event => event.preventDefault());
reconnectModal.addEventListener("close", () => {
    if (!connected) {
        reconnectModal.showModal();
    }
});

document.getElementById("components-reconnect-button").addEventListener("click", retry);
document.getElementById("components-resume-button").addEventListener("click", resume);

function handleReconnectStateChanged(event) {
    const state = event.detail.state;
    connected = state === "hide";
    if (state === "show" || state === "paused") {
        if (!reconnectModal.open) {
            reconnectModal.showModal();
        }
    } else if (state === "hide") {
        reconnectModal.close();
    } else if (state === "failed") {
        // 多半是網路斷了：等使用者回到這個分頁再試，不在背景空轉。
        document.addEventListener("visibilitychange", retryWhenDocumentBecomesVisible);
    } else if (state === "rejected") {
        // 伺服器端的 circuit 已經不在（重新啟動、逾時被回收）：只能重新載入。
        location.reload();
    }
}

async function retry() {
    document.removeEventListener("visibilitychange", retryWhenDocumentBecomesVisible);

    try {
        // true 成功；false 連得到伺服器但 circuit 已不在，改試著從保存的狀態接續，接不上就重新載入；例外是連不到伺服器。
        const successful = await Blazor.reconnect();
        if (!successful) {
            const resumed = await Blazor.resumeCircuit();
            if (!resumed) {
                location.reload();
            } else {
                connected = true;
                reconnectModal.close();
            }
        }
    } catch {
        document.addEventListener("visibilitychange", retryWhenDocumentBecomesVisible);
    }
}

async function resume() {
    try {
        const successful = await Blazor.resumeCircuit();
        if (!successful) {
            location.reload();
        }
    } catch {
        reconnectModal.classList.replace("components-reconnect-paused", "components-reconnect-resume-failed");
    }
}

async function retryWhenDocumentBecomesVisible() {
    if (document.visibilityState === "visible") {
        await retry();
    }
}
