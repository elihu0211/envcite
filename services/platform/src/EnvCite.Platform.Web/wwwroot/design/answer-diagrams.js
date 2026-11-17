// 漸進式 AI 圖表（Mermaid）渲染器：偵測畫面上出現圖表區塊時才依指紋路徑延遲載入自託管 mermaid.min.js
// （不發起任何第三方字型或 CDN 請求）。圖的配色與字型全由 platform.css 以角色變數宣告，
// 這裡不解析色碼、也不隨深淺色重畫：主題一換，圖跟著頁面的變數一起變。
(() => {
    const currentScript = document.currentScript;
    const mermaidSrc = currentScript?.dataset?.mermaidSrc || "/_content/EnvCite.Platform.Web/design/mermaid.min.js";

    let loadPromise = null;
    let renderChain = Promise.resolve();
    let diagramCounter = 0;
    let initialized = false;
    let rafHandle = 0;

    function loadMermaid() {
        if (window.mermaid) return Promise.resolve(window.mermaid);
        if (loadPromise) return loadPromise;

        loadPromise = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = mermaidSrc;
            script.async = true;
            script.onload = () => {
                if (window.mermaid) resolve(window.mermaid);
                else {
                    loadPromise = null;
                    reject(new Error("mermaid global missing after script load"));
                }
            };
            script.onerror = (err) => {
                loadPromise = null;
                reject(err);
            };
            document.head.appendChild(script);
        });
        return loadPromise;
    }

    async function renderPendingDiagrams() {
        const hosts = Array.from(document.querySelectorAll(".ep-mermaid[data-mermaid-source]"));
        const pending = hosts.filter((el) => el.dataset.diagramState !== "ready");
        if (pending.length === 0) return;

        let mermaid;
        try {
            mermaid = await loadMermaid();
        } catch {
            // 載入失敗時保留 <pre> 原文降級呈現。
            return;
        }

        if (!initialized) {
            mermaid.initialize({
                startOnLoad: false,
                securityLevel: "strict",
                suppressErrorRendering: true,
                theme: "base",
                fontFamily: "var(--ep-font)",
            });
            initialized = true;
        }

        for (const host of pending) {
            if (!host.isConnected) continue;

            const source = host.getAttribute("data-mermaid-source") || "";
            const canvas = host.querySelector(".ep-mermaid-canvas");
            const fallback = host.querySelector(".ep-mermaid-fallback");
            if (!source.trim() || !canvas) continue;

            diagramCounter += 1;
            const renderId = `ep-mmd-${diagramCounter}`;
            try {
                const result = await mermaid.render(renderId, source);
                if (!host.isConnected) continue;
                canvas.innerHTML = result.svg;
                result.bindFunctions?.(canvas);
                canvas.removeAttribute("hidden");
                if (fallback) fallback.hidden = true;
                host.dataset.diagramState = "ready";
            } catch {
                if (!host.isConnected) continue;
                canvas.innerHTML = "";
                canvas.setAttribute("hidden", "");
                if (fallback) fallback.hidden = false;
                host.dataset.diagramState = "fallback";
            }
        }
    }

    function scheduleRender() {
        if (rafHandle) return;
        rafHandle = requestAnimationFrame(() => {
            rafHandle = 0;
            renderChain = renderChain.then(renderPendingDiagrams).catch(() => {});
        });
    }

    function startObservers() {
        if (!document.body) return;
        new MutationObserver((mutations) => {
            for (const m of mutations) {
                if (m.addedNodes.length > 0) {
                    scheduleRender();
                    return;
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
    }

    window.epDiagrams = { renderAll: scheduleRender };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            startObservers();
            scheduleRender();
        });
    } else {
        startObservers();
        scheduleRender();
    }

    document.addEventListener("enhancedload", scheduleRender);
})();
