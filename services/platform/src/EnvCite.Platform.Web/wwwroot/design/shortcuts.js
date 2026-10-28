// ⌘K（Mac）／Ctrl+K 開啟後台頂欄的切換器：只是替按鈕按一下；切換器是原生 popover，內容在伺服器端的元件裡。
(() => {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);

    document.addEventListener("keydown", (event) => {
        if ((mac ? event.metaKey : event.ctrlKey) && event.key.toLowerCase() === "k") {
            const button = document.getElementById("ep-palette-open");
            if (button) {
                event.preventDefault();
                button.click();
            }
        }
    });

    // 點了切換器裡的結果就收起；在搜尋框按 Enter 等於點第一筆（連結由 Blazor 的導覽接手）。
    const palette = () => document.getElementById("ep-palette");
    document.addEventListener("click", (event) => {
        if (event.target.closest("#ep-palette a")) {
            palette()?.hidePopover();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.closest?.("#ep-palette input")) {
            event.preventDefault();
            palette()?.querySelector("a")?.click();
        }
    });

    // 提示字依平台顯示；沒有 JavaScript 時維持預設的 Ctrl K。
    const hint = () => document.querySelectorAll("[data-shortcut-hint]").forEach((el) => { el.textContent = mac ? "⌘K" : "Ctrl K"; });
    document.addEventListener("DOMContentLoaded", hint);
    window.addEventListener("load", hint);
})();
