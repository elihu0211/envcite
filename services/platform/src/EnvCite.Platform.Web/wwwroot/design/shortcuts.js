// ⌘K（Mac）／Ctrl+K 開啟後台頂欄的切換器：只是替按鈕按一下，開關與內容都在伺服器端的元件裡。
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

    // 提示字依平台顯示；沒有 JavaScript 時維持預設的 Ctrl K。
    const hint = () => document.querySelectorAll("[data-shortcut-hint]").forEach((el) => { el.textContent = mac ? "⌘K" : "Ctrl K"; });
    document.addEventListener("DOMContentLoaded", hint);
    window.addEventListener("load", hint);
})();
