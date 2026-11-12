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

    // ---- 新密碼的長度回饋 --------------------------------------------------------------------------
    // NewPasswordField 清單裡「長度」那一條：夠了就打勾，不夠就說還差幾個。長度用 value.length（UTF-16，與伺服器端同一種算法）。
    // 畫面上每個字元都更新；螢幕閱讀器只在剛好跨過最短長度時聽到一次，不逐字念。
    // 最短長度與兩段文字都由伺服器端寫在 data-*，這裡不寫死任何一個。
    const guideOf = (input) => input.closest(".ep-field-row")?.querySelector(".ep-password-guide");

    const updateGuide = (input, announce) => {
        const guide = guideOf(input);
        const min = Number(guide?.dataset.min);
        if (!guide || !min) {
            return;
        }

        const length = input.value.length;
        const rule = guide.querySelector("[data-rule=length]");
        const wasMet = rule.hasAttribute("data-met");
        const met = length >= min;
        rule.toggleAttribute("data-met", met);
        rule.querySelector(".ep-password-remaining").textContent =
            length === 0 || met ? "" : guide.dataset.textShort.replace("{0}", String(min - length));

        const live = guide.querySelector(".ep-password-announce");
        if (met && !wasMet && announce) {
            live.textContent = guide.dataset.textOk;
        } else if (!met) {
            live.textContent = "";
        }
    };

    document.addEventListener("input", (event) => {
        if (event.target instanceof HTMLInputElement && event.target.closest(".ep-password")) {
            updateGuide(event.target, true);
        }
    });

    // 密碼管理員自動填入、上一頁還原：頁面載入與還原時依目前的值畫一次（不通知）。
    const renderAllGuides = () => document.querySelectorAll(".ep-password-guide").forEach((guide) => {
        const input = guide.closest(".ep-field-row")?.querySelector(".ep-password input");
        if (input) {
            updateGuide(input, false);
        }
    });
    renderAllGuides();
    document.addEventListener("DOMContentLoaded", renderAllGuides);
    window.addEventListener("pageshow", renderAllGuides);

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

    // ---- 抽屜的手勢：左緣滑出、往左滑收起 ----------------------------------------------------------
    // 跟手（位置 1:1）、放開時用速度投射落點、再以放開當下的速度接續動畫；隨時可以重新抓住（從目前畫面上的位置起算，
    // 不是從邏輯上的目標值）。拖過頭的那一段有橡皮筋的阻力。只收觸控與手寫筆，滑鼠不拖。
    const decelerationRate = 0.998;
    const project = (velocity /* px/s */) => (velocity / 1000) * decelerationRate / (1 - decelerationRate);
    const rubberband = (overshoot, dimension, constant = 0.55) =>
        (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));

    let drag = null;

    const sidePanel = () => document.querySelector(".ep-side");

    document.addEventListener("pointerdown", (event) => {
        const panel = sidePanel();
        if (!panel || !phone.matches || event.pointerType === "mouse" || !event.isPrimary) {
            return;
        }

        const rect = panel.getBoundingClientRect();
        const visible = rect.right > 1; // 畫面上還看得到（包含正在滑入或滑出的途中）
        const grabsPanel = event.target.closest?.(".ep-side, .ep-scrim") && visible;
        if (!event.target.closest?.(".ep-edge") && !grabsPanel) {
            return;
        }

        // 起點取目前畫面上的位置（presentation value）：抓住一個還在動的抽屜，不會跳一下。
        drag = {
            id: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            from: Math.min(0, rect.left),
            width: rect.width,
            active: false,
            samples: [{ t: event.timeStamp, x: event.clientX }],
            position: Math.min(0, rect.left),
        };
    });

    document.addEventListener("pointermove", (event) => {
        if (!drag || event.pointerId !== drag.id) {
            return;
        }

        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (!drag.active) {
            // 先看方向：垂直多於水平就是在捲動，放手給瀏覽器；水平超過一小段才接手（避免輕點被當成拖曳）。
            if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
                drag = null;
            } else if (Math.abs(dx) > 10) {
                drag.active = true;
                root.setAttribute("data-dragging", "");
                try {
                    sidePanel().setPointerCapture(event.pointerId);
                } catch {
                    // 合成事件或指標已結束：不影響拖曳，只是不鎖定。
                }
            }

            if (!drag?.active) {
                return;
            }
        }

        const raw = drag.from + dx;
        drag.position = raw > 0 ? rubberband(raw, drag.width) : Math.max(-drag.width, raw);
        root.style.setProperty("--ep-drawer-x", `${drag.position}px`);
        root.style.setProperty("--ep-drawer-progress", String(1 + drag.position / drag.width));
        drag.samples.push({ t: event.timeStamp, x: event.clientX });
        drag.samples = drag.samples.filter((sample) => event.timeStamp - sample.t < 120);
    });

    const endDrag = (event) => {
        if (!drag || event.pointerId !== drag.id) {
            return;
        }

        const { active, position, width, samples } = drag;
        drag = null;
        if (!active) {
            return;
        }

        // 放開當下的速度（px/s）：取最近一小段的位移除以時間。
        const first = samples[0];
        const last = samples[samples.length - 1];
        const elapsed = Math.max(last.t - first.t, 1);
        const velocity = ((last.x - first.x) / elapsed) * 1000;

        // 依投射的落點決定開或關（越過一半就開）：很快地往右一甩，即使只拖了一點也會開。
        const open = position + project(velocity) > -width / 2;
        const target = open ? 0 : -width;
        const remaining = Math.abs(target - position);

        // 速度承接：剩下的距離除以放開時朝落點的速度，限制在一個舒服的範圍；沒什麼速度就用預設時長。
        const toward = open ? Math.max(velocity, 0) : Math.max(-velocity, 0);
        const duration = toward > 50 ? Math.min(Math.max((remaining / toward) * 2000, 160), 420) : null;
        if (duration) {
            root.style.setProperty("--ep-drawer-duration", `${Math.round(duration)}ms`);
        }

        root.removeAttribute("data-dragging");
        root.toggleAttribute("data-drawer", open);
        root.style.removeProperty("--ep-drawer-x");
        root.style.removeProperty("--ep-drawer-progress");
        syncSide();

        // 拖曳之後瀏覽器仍會送出一次 click（落在遮罩或連結上）：吃掉它，不然抽屜剛開就被點掉、或連結被誤點。
        const swallow = (clickEvent) => clickEvent.stopPropagation();
        window.addEventListener("click", swallow, { capture: true, once: true });
        setTimeout(() => window.removeEventListener("click", swallow, true), 50);
        setTimeout(() => root.style.removeProperty("--ep-drawer-duration"), (duration ?? 200) + 80);
    };

    document.addEventListener("pointerup", endDrag);
    document.addEventListener("pointercancel", endDrag);

    // ---- 六格驗證碼 --------------------------------------------------------------------------------
    // 真的輸入框在方格上面（透明）：輸入、貼上、簡訊自動填入、無障礙都走它；這裡只把值畫進方格、標出目前那一格，
    // 把游標固定在最後（點哪一格都一樣，不會在中間插入），輸滿且標了 data-autosubmit 就自動送出。
    const otpField = (element) => element instanceof Element ? element.closest(".ep-otp") : null;

    const renderOtp = (otp) => {
        const input = otp.querySelector("input");
        const length = Number(otp.dataset.length) || 6;
        const digits = input.value.replace(/\D/g, "").slice(0, length);
        if (input.value !== digits) {
            input.value = digits;
        }

        const active = document.activeElement === input ? Math.min(digits.length, length - 1) : -1;
        otp.querySelectorAll(".ep-otp-slot").forEach((slot, index) => {
            slot.textContent = digits[index] ?? "";
            slot.toggleAttribute("data-filled", index < digits.length);
            slot.toggleAttribute("data-active", index === active);
        });
        return digits.length === length;
    };

    const caretToEnd = (input) => input.setSelectionRange(input.value.length, input.value.length);

    document.addEventListener("input", (event) => {
        const otp = otpField(event.target);
        if (otp && renderOtp(otp) && "autosubmit" in otp.dataset) {
            const form = event.target.form;
            if (form?.checkValidity()) {
                form.requestSubmit();
            }
        }
    });

    document.addEventListener("focusin", (event) => {
        const otp = otpField(event.target);
        if (otp) {
            caretToEnd(event.target);
            renderOtp(otp);
        }
    });

    document.addEventListener("focusout", (event) => {
        const otp = otpField(event.target);
        if (otp) {
            renderOtp(otp);
        }
    });

    document.addEventListener("mouseup", (event) => {
        if (otpField(event.target)) {
            caretToEnd(event.target);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (otpField(event.target) && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            caretToEnd(event.target);
        }
    });

    // 貼上：只取數字，整串取代目前的內容（貼六位數就填滿六格）。
    document.addEventListener("paste", (event) => {
        const otp = otpField(event.target);
        const digits = (event.clipboardData?.getData("text") ?? "").replace(/\D/g, "");
        if (otp && digits) {
            event.preventDefault();
            event.target.value = digits;
            event.target.dispatchEvent(new Event("input", { bubbles: true }));
        }
    });

    const renderAllOtp = () => document.querySelectorAll(".ep-otp").forEach(renderOtp);
    renderAllOtp();
    document.addEventListener("DOMContentLoaded", renderAllOtp);
    window.addEventListener("load", renderAllOtp);
    window.addEventListener("pageshow", renderAllOtp);
    // autofocus 在頁面載入之後才套用，而且頁面沒有焦點時不會有 focusin：多補兩個時機，目前那一格才一定亮起來。
    window.addEventListener("focus", renderAllOtp);
    requestAnimationFrame(() => requestAnimationFrame(renderAllOtp));

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
        // 上一次列印的 iframe 要等 afterprint 才會移除；取消、或瀏覽器沒送出事件時會留著，再印一次前先清掉。
        document.querySelectorAll("iframe.ep-print-frame").forEach((old) => old.remove());
        const frame = document.createElement("iframe");
        frame.className = "ep-print-frame";
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

    // 把一段文字的複製鈕做成「複製→已複製」兩秒後還原；復原碼與一次性密碼共用。
    const confirmCopy = (button, status) => {
        const label = button.querySelector("span");
        const original = label.textContent;
        label.textContent = button.dataset.done;
        button.toggleAttribute("data-done-now", true);
        if (status) {
            status.textContent = button.dataset.done;
        }

        setTimeout(() => {
            label.textContent = original;
            button.removeAttribute("data-done-now");
            if (status) {
                status.textContent = "";
            }
        }, 2000);
    };

    // 剪貼簿與老方法都不能用時的最後退路：把文字顯示出來（平常收起來）並選取，使用者自己按 Ctrl+C。提示文字平常只給
    // 螢幕閱讀器，這時要讓看得到的人也讀到，所以暫時拿掉 ep-visually-hidden。
    const selectForManualCopy = (target, button, status) => {
        target.setAttribute("data-reveal", "");
        const selection = getSelection();
        const range = document.createRange();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
        if (status && button.dataset.failed) {
            status.textContent = button.dataset.failed;
            status.classList.remove("ep-visually-hidden");
            setTimeout(() => {
                status.textContent = "";
                status.classList.add("ep-visually-hidden");
            }, 8000);
        }
    };

    // 複製鈕：data-copy-target 指向要複製的元素；可選的 data-copy-status 指向 aria-live 區（念出「已複製」或失敗提示）、
    // data-done 是成功時的鈕文字、data-failed 是失敗提示。複製的是元素裡的完整文字，不是畫面上換行後的樣子。
    document.addEventListener("click", async (event) => {
        const copyButton = event.target.closest?.("[data-copy-target]");
        if (copyButton) {
            const target = document.getElementById(copyButton.dataset.copyTarget);
            const status = document.getElementById(copyButton.dataset.copyStatus ?? "");
            const text = target?.textContent.trim();
            if (text) {
                if (await copyText(text)) {
                    confirmCopy(copyButton, status);
                } else {
                    selectForManualCopy(target, copyButton, status);
                }
            }

            return;
        }

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
            confirmCopy(button, box.querySelector("[data-recovery-status]"));
        }
    });
})();
