// 滾動更新後，舊分頁仍引用舊版的指紋檔（頭像裁切、圖表渲染是事後才延遲載入的腳本），新 Pod 上已沒有它們，
// 載入會失敗，功能就靜靜地壞掉，使用者只會覺得是 bug。主流做法（Vite 的 vite:preloadError、Next.js 的 ChunkLoadError）
// 是偵測到版本偏移就重新載入一次，換成新版頁面。
// 只在伺服器明確說檔案已不存在時才重新載入：網路中斷或 5xx 多半是暫時的，重新載入也解不了；
// 一分鐘內只重新載入一次，檔案真的遺失時才不會無限重載。
(() => {
    const guardKey = "ep-asset-reload";
    const guardWindow = 60000;
    let reloading = false;

    window.addEventListener("error", event => {
        // 資源載入失敗的 error 事件不會冒泡，要在捕獲階段接。
        const target = event.target;
        const url = target instanceof HTMLScriptElement ? target.src
            : target instanceof HTMLLinkElement && target.rel === "stylesheet" ? target.href
                : "";
        if (url && new URL(url, location.href).origin === location.origin) {
            void reloadWhenMissing(url);
        }
    }, true);

    async function reloadWhenMissing(url) {
        if (reloading || reloadedRecently()) {
            return;
        }

        try {
            const response = await fetch(url, { method: "HEAD", cache: "no-store" });
            // 找不到的網址可能被路由接走而回 200 的 HTML 頁，所以內容類型是 HTML 也算檔案不存在。
            const missing = response.status === 404
                || (response.ok && (response.headers.get("content-type") ?? "").includes("text/html"));
            if (!missing) {
                return;
            }
        } catch {
            return;
        }

        // 多個檔案同時失敗時只讓第一個進來；等待期間可能已有別處重新載入。
        if (reloading || reloadedRecently() || !markReloaded()) {
            return;
        }

        reloading = true;
        location.reload();
    }

    function reloadedRecently() {
        try {
            const last = Number(sessionStorage.getItem(guardKey));
            return last > 0 && Date.now() - last < guardWindow;
        } catch {
            // 讀不到儲存空間就沒辦法防迴圈，寧可不重新載入。
            return true;
        }
    }

    function markReloaded() {
        try {
            sessionStorage.setItem(guardKey, String(Date.now()));
            return true;
        } catch {
            return false;
        }
    }
})();
