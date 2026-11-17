// 滾動更新、Pod 重啟與網路切換都會讓互動頁的連線斷掉；使用者不該為此做任何事，更不該因為停在提示上而以為是故障。
// 所以這裡的原則是全部自動恢復，提示只是告知：
// - 短暫斷線在 showDelay 內接回就完全不出現（滾動更新通常一兩秒內就接到新 Pod）；
// - 重試用完（failed）與暫停（paused）都自動再試，退避到上限後仍持續，直到接回或重新載入；
// - 回到分頁、網路恢復時立刻再試，不等下一輪計時；
// - circuit 已不在（rejected）直接重新載入。
// 按鈕只是「現在就試」的捷徑。與 .NET 範本相同的狀態處理之外，也不讓 Esc 關掉提示：連線斷掉時畫面已不能操作，
// 關掉提示只會留下一個看似正常、實際沒有反應的頁面。瀏覽器在使用者沒有互動過時不理會 cancel 的 preventDefault，
// 所以還要在被關掉、而連線仍未恢復時重新打開。
const showDelay = 1500;
const retryBaseDelay = 2000;
const retryMaxDelay = 30000;

const reconnectModal = document.getElementById("components-reconnect-modal");
let connected = true;
let showTimer = 0;
let retryTimer = 0;
let retryAttempts = 0;
// 排定要重試的動作（recover 或 resume）；回到分頁或網路恢復時直接執行它，不等計時。
let pendingAction = null;

reconnectModal.addEventListener("components-reconnect-state-changed", handleReconnectStateChanged);
reconnectModal.addEventListener("cancel", event => event.preventDefault());
reconnectModal.addEventListener("close", () => {
    if (!connected) {
        reconnectModal.showModal();
    }
});

document.getElementById("components-reconnect-button").addEventListener("click", recover);
document.getElementById("components-resume-button").addEventListener("click", resume);
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        runPendingAction();
    }
});
window.addEventListener("online", runPendingAction);

function handleReconnectStateChanged(event) {
    const state = event.detail.state;
    connected = state === "hide";
    if (state === "show") {
        showAfterDelay();
    } else if (state === "paused") {
        showAfterDelay();
        resume();
    } else if (state === "hide") {
        clearTimeout(showTimer);
        clearTimeout(retryTimer);
        pendingAction = null;
        retryAttempts = 0;
        reconnectModal.close();
    } else if (state === "failed") {
        // 框架內建的重試用完了：多半是網路斷了或新 Pod 還沒就緒，換我們接手繼續試。
        schedule(recover);
    } else if (state === "rejected") {
        // 伺服器端的 circuit 已經不在（重新啟動、逾時被回收）：只能重新載入。
        location.reload();
    }
}

function showAfterDelay() {
    clearTimeout(showTimer);
    showTimer = setTimeout(() => {
        if (!connected && !reconnectModal.open) {
            reconnectModal.showModal();
        }
    }, showDelay);
}

function schedule(action) {
    clearTimeout(retryTimer);
    pendingAction = action;
    const delay = Math.min(retryBaseDelay * 2 ** retryAttempts, retryMaxDelay);
    retryAttempts++;
    retryTimer = setTimeout(runPendingAction, delay);
}

function runPendingAction() {
    const action = pendingAction;
    clearTimeout(retryTimer);
    pendingAction = null;
    if (action) {
        action();
    }
}

async function recover() {
    clearTimeout(retryTimer);
    pendingAction = null;

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
        schedule(recover);
    }
}

async function resume() {
    clearTimeout(retryTimer);
    pendingAction = null;

    try {
        const successful = await Blazor.resumeCircuit();
        if (!successful) {
            location.reload();
        }
    } catch {
        reconnectModal.classList.replace("components-reconnect-paused", "components-reconnect-resume-failed");
        schedule(resume);
    }
}
