// 表單的漸進增強。沒有這支腳本，每一頁仍是普通的 POST 表單：密碼欄是普通的密碼欄，復原碼可以下載。
// 事件一律委派在 document：Blazor 的強化導覽會換掉頁面內容，掛在元素上的監聽器會跟著消失。
(() => {
    // 樣式表靠這個屬性決定要不要顯示需要 JavaScript 的控制項（眼睛鈕、複製、列印）。
    document.documentElement.dataset.js = "";

    // ---- 密碼顯示／隱藏 ----------------------------------------------------------------------------
    const passwordOf = (toggle) => toggle.closest(".ep-password")?.querySelector("input");

    const setVisible = (input, toggle, visible) => {
        // 換 type 會讓部分瀏覽器丟掉游標位置：記下來再還原，改到一半的密碼不會跳到最後面。
        const { selectionStart, selectionEnd } = input;
        const focused = document.activeElement === input;
        input.type = visible ? "text" : "password";
        toggle.toggleAttribute("data-visible", visible);
        toggle.setAttribute("aria-label", visible ? toggle.dataset.labelHide : toggle.dataset.labelShow);
        if (focused && selectionStart !== null) {
            input.setSelectionRange(selectionStart, selectionEnd);
        }
    };

    const hideAll = (scope) => scope.querySelectorAll(".ep-password-toggle[data-visible]").forEach((toggle) => {
        const input = passwordOf(toggle);
        if (input) {
            setVisible(input, toggle, false);
        }
    });

    // 按鈕不搶走輸入框的焦點：手機上鍵盤不會收起來，輸入也不會被打斷。
    document.addEventListener("mousedown", (event) => {
        if (event.target.closest?.(".ep-password-toggle")) {
            event.preventDefault();
        }
    });

    document.addEventListener("click", (event) => {
        const toggle = event.target.closest?.(".ep-password-toggle");
        const input = toggle && passwordOf(toggle);
        if (input) {
            setVisible(input, toggle, input.type === "password");
        }
    });

    // 離開頁面前一律蓋回去：送出時瀏覽器才認得這是密碼欄（會問要不要存），上一頁還原時也不會露出明文。
    document.addEventListener("submit", (event) => hideAll(event.target), true);
    window.addEventListener("pagehide", () => hideAll(document));

    // ---- 大寫鎖定提醒 ------------------------------------------------------------------------------
    const capsNote = (input) => input.closest(".ep-field-row")?.querySelector(".ep-password-caps");

    const checkCaps = (event) => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || !input.closest(".ep-password") || typeof event.getModifierState !== "function") {
            return;
        }

        const note = capsNote(input);
        if (note) {
            // 密碼顯示出來時看得到自己打了什麼，不必提醒。
            note.textContent = input.type === "password" && event.getModifierState("CapsLock") ? note.dataset.text : "";
        }
    };

    document.addEventListener("keydown", checkCaps);
    document.addEventListener("keyup", checkCaps);
    document.addEventListener("focusout", (event) => {
        const note = event.target instanceof HTMLInputElement && capsNote(event.target);
        if (note) {
            note.textContent = "";
        }
    });

    // ---- 送出中的回饋 ------------------------------------------------------------------------------
    // 按下送出後按鈕立刻變成「處理中」，也擋掉連點的第二次送出。等一拍再看有沒有人（Blazor 的互動表單）接手：
    // 被接手的送出不會離開頁面，不能卡在處理中。
    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.method !== "post") {
            return;
        }

        if ("busy" in form.dataset) {
            event.preventDefault();
            return;
        }

        const submitter = event.submitter ?? form.querySelector("[type=submit]");
        setTimeout(() => {
            if (!event.defaultPrevented) {
                form.dataset.busy = "";
                submitter?.setAttribute("aria-busy", "true");
            }
        }, 0);
    });

    // 從上一頁／下一頁回來時，瀏覽器可能原封不動還原頁面（包含「處理中」）。
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) {
            document.querySelectorAll("form[data-busy]").forEach((form) => {
                delete form.dataset.busy;
                form.querySelectorAll("[aria-busy]").forEach((button) => button.removeAttribute("aria-busy"));
            });
        }
    });

    // ---- 側欄：桌機收合成窄欄、手機是抽屜 ----------------------------------------------------------
    // 偏好存在 localStorage，頁面一載入就由 <head> 裡那段小腳本套上 data-side（不閃一下）。抽屜的 data-drawer 不存檔。
    const root = document.documentElement;
    const phone = matchMedia("(width < 48rem)");
    const sideToggle = () => document.querySelector(".ep-side-toggle");

    // 按鈕的展開狀態與窄欄時的 title 提示（展開時文字就在旁邊，不需要提示）。
    const syncSide = () => {
        const collapsed = !phone.matches && root.dataset.side === "collapsed";
        sideToggle()?.setAttribute("aria-expanded", String(phone.matches ? root.hasAttribute("data-drawer") : !collapsed));
        document.querySelectorAll(".ep-side-link").forEach((link) => {
            const label = link.querySelector(".ep-side-label")?.textContent.trim();
            if (collapsed && label) {
                link.setAttribute("title", label);
            } else {
                link.removeAttribute("title");
            }
        });
    };

    const closeDrawer = (restoreFocus) => {
        if (root.hasAttribute("data-drawer")) {
            root.removeAttribute("data-drawer");
            syncSide();
            if (restoreFocus) {
                sideToggle()?.focus();
            }
        }
    };

    document.addEventListener("click", (event) => {
        if (event.target.closest?.(".ep-side-toggle")) {
            if (phone.matches) {
                root.toggleAttribute("data-drawer");
                if (root.hasAttribute("data-drawer")) {
                    document.querySelector(".ep-side-link")?.focus();
                }
            } else {
                const collapsed = root.dataset.side === "collapsed";
                if (collapsed) {
                    delete root.dataset.side;
                } else {
                    root.dataset.side = "collapsed";
                }

                try {
                    localStorage.setItem("ep-side", collapsed ? "expanded" : "collapsed");
                } catch {
                    // 沒有儲存空間（無痕視窗）：這次有效，下次回到展開。
                }
            }

            syncSide();
        } else if (event.target.closest?.(".ep-scrim")) {
            closeDrawer(true);
        } else if (event.target.closest?.(".ep-side a")) {
            closeDrawer(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeDrawer(true);
        }
    });

    // 轉向或調整視窗跨過手機的寬度：抽屜收起，按鈕狀態與提示跟著換。
    phone.addEventListener("change", () => {
        root.removeAttribute("data-drawer");
        syncSide();
    });
    syncSide();
    document.addEventListener("DOMContentLoaded", syncSide);

    // ---- 復原碼：複製、列印 ------------------------------------------------------------------------
    const copyText = async (text) => {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            // 非安全環境或權限被擋：退回選取再 copy 的老方法。
            const area = document.createElement("textarea");
            area.value = text;
            area.setAttribute("readonly", "");
            area.style.position = "fixed";
            area.style.insetBlockStart = "0";
            area.style.opacity = "0";
            document.body.append(area);
            area.select();
            const ok = document.execCommand("copy");
            area.remove();
            return ok;
        }
    };

    // 列印只印復原碼，不印整頁：在隱藏的 iframe 裡排一份純文字再列印。
    const printText = (title, text) => {
        const frame = document.createElement("iframe");
        frame.setAttribute("aria-hidden", "true");
        frame.style.position = "fixed";
        frame.style.inlineSize = "0";
        frame.style.blockSize = "0";
        frame.style.border = "0";
        document.body.append(frame);

        const doc = frame.contentDocument;
        doc.title = title;
        const pre = doc.createElement("pre");
        pre.textContent = text;
        pre.style.font = "1rem/1.8 ui-monospace, Menlo, Consolas, monospace";
        pre.style.whiteSpace = "pre-wrap";
        doc.body.append(pre);

        frame.contentWindow.addEventListener("afterprint", () => frame.remove());
        frame.contentWindow.focus();
        frame.contentWindow.print();
    };

    document.addEventListener("click", async (event) => {
        const button = event.target.closest?.("[data-recovery]");
        const box = button?.closest(".ep-recovery");
        if (!box) {
            return;
        }

        if (button.dataset.recovery === "print") {
            printText(button.dataset.title, box.dataset.document);
            return;
        }

        if (await copyText(box.dataset.codes)) {
            const label = button.querySelector("span");
            const original = label.textContent;
            label.textContent = button.dataset.done;
            button.toggleAttribute("data-done-now", true);
            box.querySelector("[data-recovery-status]").textContent = button.dataset.done;
            setTimeout(() => {
                label.textContent = original;
                button.removeAttribute("data-done-now");
                box.querySelector("[data-recovery-status]").textContent = "";
            }, 2000);
        }
    });
})();
