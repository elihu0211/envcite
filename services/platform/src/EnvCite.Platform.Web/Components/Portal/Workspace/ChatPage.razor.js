// 新的一個回合出現時，把它的開頭捲進畫面（問題在上、答案在下方展開），而不是停在舊的位置或硬捲到最底。
// 使用者要求減少動態時不做捲動動畫。
export function scrollToLastTurn() {
    const turns = document.querySelectorAll(".ep-turn");
    const last = turns[turns.length - 1];
    if (!last) {
        return;
    }

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    last.scrollIntoView({ block: "start", behavior: reduced ? "auto" : "smooth" });
}
