// 客戶入口工作區的端對端情境。由 WorkspaceE2ETests（xUnit）啟動伺服器、建立租戶與成員後呼叫，不單獨執行。
// 輸入全走環境變數；每個步驟印一行 OK／FAIL，任何一步失敗就以非零結束碼收尾，並把失敗當下的畫面存進 E2E_SHOTS。
import { chromium } from "playwright";

const env = (name) => {
    const value = process.env[name];
    if (!value) throw new Error(`缺少環境變數 ${name}`);
    return value;
};
const base = env("E2E_BASE");
const tenant = env("E2E_TENANT");
const email = env("E2E_EMAIL");
const initial = env("E2E_INITIAL");
const next = env("E2E_NEW_PASSWORD");
const shots = process.env.E2E_SHOTS;
const scenario = process.argv[2];
const question = process.env.E2E_QUESTION ?? "露天燃燒如何處罰";
const timeout = 15_000;

// 瀏覽器自己對跨頁轉場（@view-transition）的取消：頁面換掉時的正常現象，不是我們的錯誤；
// 訊息依 Chromium 版本不同（「was skipped」「was aborted because of invalid state …」），所以整族比對。
const knownNoise = [/^pageerror: Transition was (skipped|aborted)/];

const browser = await chromium.launch({ executablePath: process.env.E2E_CHROMIUM || undefined });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await context.newPage();
const problems = [];
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text().slice(0, 200)}`);
});

let current = "start";
const step = async (name, action) => {
    current = name;
    await action();
    console.log(`OK   ${name}`);
};

// 輪詢到條件成立；逾時就丟出說明。Playwright 的 locator 斷言在 @playwright/test，這裡只需要這一個。
const until = async (what, probe) => {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
        last = await probe();
        if (last === true) return;
        await page.waitForTimeout(100);
    }
    throw new Error(`${what}（最後的狀態：${JSON.stringify(last)}）`);
};

const turns = () => page.locator(".ep-turn");
// 還在等結果的回合：等待中的提示文字；列出條文時的「找到 N 條」也是 ep-status，不能用樣式判斷。
const pending = () => page.locator(".ep-turn", { hasText: "正在查找法源" });

/** 預先渲染的頁面在 circuit 連上之前，輸入框是停用的；可用了，就是 circuit 已經接手。 */
const circuitConnected = () =>
    until("circuit 沒有連上（輸入框一直是停用的）", async () => await page.locator("#query").isEnabled().catch(() => false));

const answered = (count) =>
    until(`應有 ${count} 個已回答的回合`, async () => (await turns().count()) === count && (await pending().count()) === 0);

const submit = async (text) => {
    await page.fill("#query", text);
    await page.click("button.ep-send");
};

const signIn = async () => {
    await page.goto(`${base}/portal/${tenant}/login`);
    await page.fill("input[name='Input.Email']", email);
    await page.fill("input[name='Input.Password']", initial);
    await page.click("form:has(input[name='Input.Email']) button[type=submit]");
    await page.waitForURL(/\/password$/, { timeout });

    await page.fill("input[name='Input.Current']", initial);
    await page.fill("input[name='Input.New']", next);
    await page.fill("input[name='Input.Confirm']", next);
    await page.click("form:has(input[name='Input.New']) button[type=submit]");
    await page.waitForURL(new RegExp(`/portal/${tenant}$`), { timeout });
    await circuitConnected();
};

const scenarios = {
    // 登入、問、接著問、重新整理、開新對話、回舊對話、不存在的對話。
    async flow() {
        await step("登入後 circuit 認得這位成員（沒有被導去後台登入）", async () => {
            await signIn();
            if (page.url().includes("/ui/login")) throw new Error(`被導去後台登入：${page.url()}`);
            await until("應是還沒問的空畫面", async () => (await page.locator(".ep-ask[data-state='empty']").count()) === 1);
        });
        await step("問第一題：回答帶出處，網址換成這串對話", async () => {
            await submit(question);
            await answered(1);
            await until("網址應是 /c/{id}", async () => /\/c\/[0-9a-f-]{36}$/.test(page.url()));
            const text = await turns().first().innerText();
            if (!text.includes(question) || !text.includes("L1")) throw new Error(`回合內容不對：${text}`);
        });
        await step("送出後輸入框清空且取得焦點", async () => {
            await until("輸入框應清空並取得焦點", async () =>
                (await page.inputValue("#query")) === "" && (await page.evaluate(() => document.activeElement?.id)) === "query");
        });
        let conversationUrl;
        await step("接著問：同一串對話，側欄只有這一串", async () => {
            conversationUrl = page.url();
            await page.keyboard.type(question);
            await page.keyboard.press("Enter");
            await answered(2);
            if (page.url() !== conversationUrl) throw new Error(`對話網址變了：${conversationUrl} → ${page.url()}`);
            await until("側欄應只有一則最近對話", async () => (await page.locator(".ep-recent-link").count()) === 1);
        });
        await step("重新整理：從歷史還原兩個回合", async () => {
            await page.reload();
            await circuitConnected();
            await until("應還原兩個回合", async () => (await turns().count()) === 2);
        });
        await step("側欄的「查詢」開新對話、最近對話回到舊的那一串", async () => {
            await page.click(`a.ep-side-link[href='/portal/${tenant}']`);
            await until("應是空畫面", async () => (await page.locator(".ep-ask[data-state='empty']").count()) === 1);
            await page.click(".ep-recent-link");
            await until("應回到兩個回合", async () => (await turns().count()) === 2);
        });
        await step("不存在的對話是同一個「找不到」畫面", async () => {
            await page.goto(`${base}/portal/${tenant}/c/00000000-0000-4000-8000-000000000000`);
            await until("應顯示找不到", async () => (await page.locator("body").innerText()).includes("找不到這個對話"));
        });
    },

    // 側欄的對話選單（原生 popover 加普通表單）：釘選、重新命名、點進去接續（帶著前文再問）、刪除。
    async "history-menu"() {
        const menuOf = () => page.locator(".ep-recent-item").first();
        const openMenu = async (item = menuOf()) => {
            await item.locator(".ep-recent-more").click();
            await until("選單應該打開", async () => (await page.locator(".ep-conv-menu:popover-open").count()) === 1);
        };
        // 表單送出後是整頁導回原來的頁面：等 circuit 重新接上。
        const reloaded = async () => {
            await page.waitForLoadState("load");
            await circuitConnected();
        };

        await step("登入並問第一題", async () => {
            await signIn();
            await submit(question);
            await answered(1);
            await until("網址應是 /c/{id}", async () => /\/c\/[0-9a-f-]{36}$/.test(page.url()));
            // 失敗時要看得到側欄實際有什麼：只回 false 看不出是沒更新還是多了一串。
            await until("側欄應有這一串", async () => {
                const count = await page.locator(".ep-recent-link").count();
                return count === 1 ? true : { count, side: (await page.locator(".ep-side").innerText().catch(() => "")).slice(0, 200) };
            });
        });
        const conversationUrl = page.url();

        await step("釘選：側欄多一個「已釘選」群組，停留在同一頁", async () => {
            await openMenu();
            await page.locator(".ep-conv-menu button", { hasText: /^釘選$/ }).click();
            await reloaded();
            await until("應有已釘選群組", async () =>
                (await page.locator(".ep-recent .ep-side-group", { hasText: "已釘選" }).count()) === 1);
            if (page.url() !== conversationUrl) throw new Error(`釘選後網址變了：${conversationUrl} → ${page.url()}`);
        });

        await step("重新命名：側欄與標題換成新名字", async () => {
            await openMenu();
            await page.locator(".ep-conv-rename input[name='title']").fill("我的稽查案");
            await page.locator(".ep-conv-rename input[name='title']").press("Enter");
            await reloaded();
            await until("側欄應顯示新標題", async () => (await page.locator(".ep-recent-link").first().innerText()).includes("我的稽查案"));
        });

        await step("分叉：前往複製出來的新一串，側欄多一筆、標題帶（分叉）", async () => {
            await openMenu();
            await page.locator(".ep-conv-menu button", { hasText: /^分叉$/ }).click();
            await reloaded();
            await until("網址應是另一串 /c/{id}", async () => /\/c\/[0-9a-f-]{36}$/.test(page.url()) && page.url() !== conversationUrl);
            await until("新的一串應有同樣的一個回合", async () => (await turns().count()) === 1);
            await until("側欄應有兩串，其中一串帶（分叉）", async () =>
                (await page.locator(".ep-recent-link").count()) === 2 && (await page.locator(".ep-recent-link", { hasText: "（分叉）" }).count()) === 1);
        });

        await step("封存：從側欄移到已封存檢視，取消封存就回來", async () => {
            const forkItem = () => page.locator(".ep-recent-item", { hasText: "（分叉）" });
            await openMenu(forkItem());
            await page.locator(".ep-conv-menu:popover-open button", { hasText: /^封存$/ }).click();
            await reloaded();
            await until("封存正在看的那串應回首頁", async () => page.url() === `${base}/portal/${tenant}`);
            await until("側欄應只剩原本那串", async () => (await page.locator(".ep-recent-link").count()) === 1);

            await page.goto(`${base}/portal/${tenant}/history?view=archived`);
            await until("已封存檢視應列出分叉", async () => (await page.locator(".ep-history-link", { hasText: "（分叉）" }).count()) === 1);
            await page.locator(".ep-history-item .ep-recent-more").first().click();
            await page.locator(".ep-conv-menu:popover-open button", { hasText: /^取消封存$/ }).click();
            await page.waitForLoadState("load");
            await until("取消封存後已封存檢視應是空的", async () => (await page.locator(".ep-history-link").count()) === 0);

            await page.goto(`${base}/portal/${tenant}/history`);
            await until("取消封存後預設列表應有兩串", async () => (await page.locator(".ep-history-link").count()) === 2);
            // 把分叉再封存起來，後面的步驟仍是只有一串的側欄。
            await page.locator(".ep-history-item", { hasText: "（分叉）" }).locator(".ep-recent-more").click();
            await page.locator(".ep-conv-menu:popover-open button", { hasText: /^封存$/ }).click();
            await page.waitForLoadState("load");
            await page.goto(conversationUrl);
            await circuitConnected();
            await until("側欄應只剩原本那串", async () => (await page.locator(".ep-recent-link").count()) === 1);
        });

        await step("側欄即時篩選：過濾不符的對話並用 Esc 還原，收合後點搜尋鈕可展開聚焦", async () => {
            const filter = page.locator("input[data-recent-filter]");
            await page.locator(".ep-side-head .ep-side-toggle").click();
            await until("側欄應收合", async () => (await page.evaluate(() => document.documentElement.dataset.side)) === "collapsed");
            await page.locator(".ep-recent-jump").click();
            await until("點搜尋鈕應展開側欄並聚焦篩選框", async () =>
                (await page.evaluate(() => document.documentElement.dataset.side)) === undefined &&
                (await page.evaluate(() => document.activeElement?.id)) === "ep-recent-filter");
            await filter.fill("不存在的關鍵字");
            await until("不符時應隱藏對話並顯示空狀態", async () =>
                (await page.locator(".ep-recent-item:visible").count()) === 0 && await page.locator("[data-recent-empty]").isVisible());
            await filter.press("Escape");
            await until("Esc 應還原側欄對話清單", async () =>
                (await page.locator(".ep-recent-item:visible").count()) === 1 && !(await page.locator("[data-recent-empty]").isVisible()));
        });

        await step("離開再從側欄點回來：還原回合，接著問會帶著前文", async () => {
            await page.click(`a.ep-side-link[href='/portal/${tenant}']`);
            await until("應是空畫面", async () => (await page.locator(".ep-ask[data-state='empty']").count()) === 1);
            await page.click(".ep-recent-link");
            await until("應還原一個回合", async () => (await turns().count()) === 1);
            await submit("那罰款多少"); // 單獨看沒有證據；有前文才會被改寫成可檢索的問句
            await answered(2);
            const text = await turns().last().innerText();
            if (!text.includes("L1")) throw new Error(`追問沒有得到有出處的回答：${text}`);
        });

        await step("追問前檢查回答工具列：複製回答、法源依據抽屜與針對本條追問", async () => {
            const lastTurn = turns().last();
            const copyBtn = lastTurn.locator(".ep-answer-action[data-copy-target]");
            await copyBtn.click();
            await until("複製後應顯示已複製", async () => (await copyBtn.innerText()).includes("已複製"));
            await lastTurn.locator(".ep-answer-action", { hasText: "法源依據" }).click();
            await until("點工具列應開啟右側條文抽屜", async () => (await page.locator(".ep-drawer-panel").count()) === 1);
            await page.click(".ep-drawer-iri-btn");
            await until("點擊條文索引碼膠囊應顯示已複製", async () =>
                await page.locator(".ep-drawer-iri-btn[data-done-now]").count() === 1 &&
                (await page.locator(".ep-drawer-iri-btn").innerText()).includes("已複製"));
            await page.click(".ep-drawer-close");
            await until("關閉後抽屜應收起", async () => (await page.locator(".ep-drawer-panel").count()) === 0);
            await lastTurn.locator(".ep-answer-action", { hasText: "法源依據" }).click();
            await until("再次開啟右側條文抽屜", async () => (await page.locator(".ep-drawer-panel").count()) === 1);
            await page.click(".ep-drawer-followup");
            await until("針對本條追問應收起抽屜並預填輸入框", async () =>
                (await page.locator(".ep-drawer-panel").count()) === 0 &&
                (await page.inputValue("#query")).includes("空氣污染防制法"));
            await page.fill("#query", "");
        });

        await step("對話紀錄頁即時篩選、Esc 還原與網址 ?q= 預填", async () => {
            await page.goto(`${base}/portal/${tenant}/history`);
            const historyFilter = page.locator("input[data-history-filter]");
            await historyFilter.fill("不存在的關鍵字");
            await until("不符時應隱藏清單並顯示空狀態", async () =>
                (await page.locator("[data-history-item]:visible").count()) === 0 && await page.locator("[data-history-empty]").isVisible());
            await historyFilter.press("Escape");
            await until("Esc 應還原紀錄清單", async () =>
                (await page.locator("[data-history-item]:visible").count()) === 1 && !(await page.locator("[data-history-empty]").isVisible()));
            await page.goto(`${base}/portal/${tenant}?q=${encodeURIComponent("噪音管制標準第5條")}`);
            await circuitConnected();
            await until("網址 ?q= 應預填輸入框", async () => (await page.inputValue("#query")) === "噪音管制標準第5條");
            await page.goto(conversationUrl);
            await circuitConnected();
        });

        await step("刪除：要先確認，刪掉後回首頁、側欄沒有這一串", async () => {
            await openMenu();
            await page.locator(".ep-conv-menu .ep-menu-danger").click();
            await until("應出現確認", async () => (await page.locator(".ep-conv-confirm:popover-open").count()) === 1);
            await page.locator(".ep-conv-confirm button[type=submit]").click();
            await reloaded();
            await until("應回到新對話", async () => page.url() === `${base}/portal/${tenant}`);
            await until("側欄不該再有對話", async () => (await page.locator(".ep-recent-link").count()) === 0);
            await page.goto(conversationUrl);
            await until("舊網址應是找不到", async () => (await page.locator("body").innerText()).includes("找不到這個對話"));
        });
    },

    // 拒答：回合留著、說明原因，之後還能再問。
    async refusal() {
        await step("登入", signIn);
        await step("不在範圍內的問題被拒答，回合留著並說明原因", async () => {
            await submit("完全不在範圍內的問題");
            await answered(1);
            if ((await turns().first().locator(".ep-alert").count()) === 0) throw new Error("拒答沒有說明");
        });
        await step("之後還能再問", async () => {
            await submit(question);
            await answered(2);
            const text = await turns().last().innerText();
            if (!text.includes("L1")) throw new Error(`第二個回合沒有出處：${text}`);
        });
    },

    // 沒有同意模型：只列條文。
    async search() {
        await step("登入", signIn);
        await step("只列有出處的條文", async () => {
            await submit("噪音管制標準");
            await answered(1);
            const text = await turns().first().innerText();
            if (!text.includes("《")) throw new Error(`沒有列出條文：${text}`);
        });
    },

    // 外殼換頁不動：在互動頁（工作區）與靜態頁（成員、帳號）之間來回，頂欄與側欄的位置與大小一模一樣；<html> 上由腳本管的
    // data-js 與收合的 data-side 不被強化導覽抹掉（沒有任何一格畫面缺它們）；期間外殼的元素沒有版面位移。
    // 靜態頁沒有 MudBlazor 的樣式表，互動頁有：這一條守的是兩種頁面的外殼是同一個，換頁不內縮、不跳。
    async shell() {
        await page.addInitScript(() => {
            window.__shifts = [];
            window.__missing = 0;
            try {
                new PerformanceObserver((list) => {
                    for (const entry of list.getEntries())
                        for (const source of entry.sources) window.__shifts.push(String(source.node?.className ?? source.node?.nodeName));
                }).observe({ type: "layout-shift", buffered: true });
            } catch { /* 沒有 layout-shift 的瀏覽器：只剩幾何與屬性的檢查 */ }
            const tick = () => {
                // 側欄已經在畫面上、而 <html> 缺 data-js：就是畫出了導覽鈕消失的那一格。
                if (document.querySelector(".ep-side") && !document.documentElement.hasAttribute("data-js")) window.__missing++;
                requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        });

        const geometry = () => page.evaluate(() => {
            const box = (selector) => {
                const r = document.querySelector(selector)?.getBoundingClientRect();
                return r ? [r.x, r.y, r.width, r.height].map(Math.round) : null;
            };
            return {
                top: box(".ep-shell-top"),
                side: box(".ep-side"),
                foot: box(".ep-side-foot"),
                js: document.documentElement.hasAttribute("data-js"),
                collapsed: document.documentElement.dataset.side ?? "",
            };
        });
        const sameShell = (expected, actual, where) => {
            if (!actual.js) throw new Error(`${where}：<html> 沒有 data-js`);
            if (actual.collapsed !== expected.collapsed) throw new Error(`${where}：收合狀態變了 ${JSON.stringify(expected.collapsed)} → ${JSON.stringify(actual.collapsed)}`);
            if (JSON.stringify(actual.top) !== JSON.stringify(expected.top)) throw new Error(`${where}：頂欄 ${JSON.stringify(expected.top)} → ${JSON.stringify(actual.top)}`);
            if (JSON.stringify(actual.side) !== JSON.stringify(expected.side)) throw new Error(`${where}：側欄 ${JSON.stringify(expected.side)} → ${JSON.stringify(actual.side)}`);
            if (JSON.stringify(actual.foot) !== JSON.stringify(expected.foot)) throw new Error(`${where}：左下角帳號列 ${JSON.stringify(expected.foot)} → ${JSON.stringify(actual.foot)}`);
        };
        // 換到 path 後等頁面穩定（動畫收尾、串流結束），再比對外殼，並檢查沒有缺屬性的畫面與外殼元素的版面位移。
        const visit = async (name, path, expected) => {
            await page.click(`a.ep-side-link[href='${path}']`);
            await until(`應換到 ${path}`, async () => new URL(page.url()).pathname === path);
            await page.waitForTimeout(600);
            sameShell(expected, await geometry(), name);
            const { shifts, missing } = await page.evaluate(() => ({ shifts: window.__shifts, missing: window.__missing }));
            if (missing > 0) throw new Error(`${name}：有 ${missing} 格畫面缺 data-js`);
            const shell = shifts.filter((c) => /ep-shell-top|ep-side|ep-brand|ep-console-body/.test(c));
            if (shell.length > 0) throw new Error(`${name}：外殼元素有版面位移 ${JSON.stringify(shell)}`);
            await page.evaluate(() => { window.__shifts = []; window.__missing = 0; });
        };

        const home = `/portal/${tenant}`;
        let expanded;
        await step("登入後在互動的工作區：記下外殼的位置", async () => {
            await signIn();
            await page.waitForTimeout(600);
            expanded = await geometry();
            if (!expanded.js) throw new Error("工作區的 <html> 沒有 data-js");
            if (expanded.side?.[0] !== 0 || expanded.side?.[1] !== 0) throw new Error(`側欄沒有從視窗左上角展開：${JSON.stringify(expanded.side)}`);
            if (expanded.top?.[0] !== expanded.side?.[2] || expanded.top?.[1] !== 0) throw new Error(`頂欄沒有接在側欄右側頂端：${JSON.stringify(expanded)}`);
            if (!expanded.foot || expanded.foot[1] < expanded.side[3] / 2) throw new Error(`帳號資料沒有位於左下角：${JSON.stringify(expanded)}`);
            const openUserMenu = await page.evaluate(async () => {
                document.querySelector(".ep-portal-user")?.click();
                const menu = document.getElementById("ep-account-menu");
                const open = menu?.matches(":popover-open") === true;
                const rect = menu?.getBoundingClientRect();
                menu?.hidePopover?.();
                return { open, left: rect?.x ?? -1, bottom: rect?.bottom ?? -1 };
            });
            if (!openUserMenu.open || openUserMenu.left > expanded.side[2]) throw new Error(`左下角帳號選單沒有從左下角展開：${JSON.stringify(openUserMenu)}`);
            await page.evaluate(() => { window.__shifts = []; window.__missing = 0; });
        });
        await step("工作區 → 成員 → 帳號 → 對話紀錄 → 工作區：外殼不動", async () => {
            await visit("成員", `${home}/members`, expanded);
            await visit("帳號", `${home}/account`, expanded);
            await visit("對話紀錄", `${home}/history`, expanded);
            await visit("工作區", home, expanded);
        });

        let collapsed;
        await step("收合側欄再來回換頁：仍是窄欄，位置不動", async () => {
            await page.click(".ep-side-toggle");
            await until("側欄應收成窄欄", async () => (await page.evaluate(() => document.documentElement.dataset.side)) === "collapsed");
            await page.waitForTimeout(600);
            collapsed = await geometry();
            if (collapsed.side[2] >= expanded.side[2]) throw new Error(`收合後側欄沒有變窄：${expanded.side[2]} → ${collapsed.side[2]}`);
            await page.evaluate(() => { window.__shifts = []; window.__missing = 0; });
            await visit("成員（收合）", `${home}/members`, collapsed);
            await visit("帳號（收合）", `${home}/account`, collapsed);
            await visit("工作區（收合）", home, collapsed);
        });
        await step("重新整理：偏好還在，第一格就是窄欄", async () => {
            await page.reload();
            await circuitConnected();
            await page.waitForTimeout(600);
            sameShell(collapsed, await geometry(), "重新整理");
        });
    },

    // 首頁的輸入區：一開就聚焦、沒有日期欄與長段說明；多行輸入自動長高；Enter 送出，Shift+Enter 換行，
    // 輸入法組字中的 Enter 與空白都不送出；範例點了就送出；說明收在原生 popover（可開、可用 Esc 關）。
    async composer() {
        const area = page.locator("textarea#query");
        const send = page.locator("button.ep-send");
        const home = `${base}/portal/${tenant}`;

        await step("首頁：輸入框已聚焦、沒有日期欄與長段說明、空白時送出鈕停用", async () => {
            await signIn();
            await until("輸入框應已聚焦", async () => (await page.evaluate(() => document.activeElement?.id)) === "query");
            if ((await page.locator("input[type=date]").count()) !== 0) throw new Error("首頁還有日期欄");
            const text = await page.locator(".ep-ask").innerText();
            if (text.includes("哪一天有效") || text.includes("不填日期")) throw new Error(`首頁還有日期的說明：${text}`);
            if (text.includes("想查哪一條法規")) throw new Error(`首頁把用途框死成只能查法規：${text}`);
            if (!(await send.isDisabled())) throw new Error("空白時送出鈕應停用");
            if ((await page.locator(".ep-examples .ep-chip").count()) < 1) throw new Error("沒有範例");
        });
        await step("Shift+Enter 換行、輸入框跟著長高、不送出、送出鈕啟用", async () => {
            const before = (await area.boundingBox()).height;
            await page.keyboard.type("噪音");
            await page.keyboard.press("Shift+Enter");
            await page.keyboard.type("管制");
            await page.keyboard.press("Shift+Enter");
            await page.keyboard.type("標準");
            await until("輸入框應含換行", async () => (await area.inputValue()).includes("\n"));
            const after = (await area.boundingBox()).height;
            if (after <= before) throw new Error(`輸入框沒有長高：${before} → ${after}`);
            await until("有字之後送出鈕應啟用", async () => await send.isEnabled());
            await page.waitForTimeout(300);
            if ((await turns().count()) !== 0) throw new Error("Shift+Enter 不該送出");
        });
        await step("輸入法組字中的 Enter 不送出", async () => {
            const prevented = await area.evaluate((element) => {
                const event = new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true });
                element.dispatchEvent(event);
                return event.defaultPrevented;
            });
            if (prevented) throw new Error("組字中的 Enter 被攔下了（會吃掉選字）");
            await page.waitForTimeout(300);
            if ((await turns().count()) !== 0) throw new Error("組字中的 Enter 不該送出");
        });
        await step("空白按 Enter 不送出", async () => {
            await area.fill("   ");
            await page.keyboard.press("Enter");
            await page.waitForTimeout(300);
            if ((await turns().count()) !== 0) throw new Error("空白不該送出");
        });
        await step("Enter 送出：得到回答、輸入框清空", async () => {
            await area.fill(question);
            await page.keyboard.press("Enter");
            await answered(1);
            await until("輸入框應清空", async () => (await area.inputValue()) === "");
        });
        await step("新對話的範例：點了就送出", async () => {
            await page.goto(home);
            await circuitConnected();
            await page.locator(".ep-examples .ep-chip").first().click();
            await answered(1);
        });
        await step("說明收在原生 popover：點開看得到資料送出的揭露，Esc 關閉", async () => {
            await page.goto(home);
            await circuitConnected();
            await page.click("button[popovertarget=ask-info]");
            const open = () => page.evaluate(() => document.getElementById("ask-info")?.matches(":popover-open") === true);
            await until("說明面板應開啟", open);
            const text = await page.locator("#ask-info").innerText();
            if (!text.includes("用條文裡會出現的詞") || !text.includes("模型服務")) throw new Error(`說明缺內容：${text}`);
            await page.keyboard.press("Escape");
            await until("Esc 後說明面板應關閉", async () => !(await open()));
        });
    },

    // 在 circuit 裡從自己的租戶切到別人的租戶（不是新的頁面請求）：入口的授權擋下，看不到對方的內容。
    async "cross-tenant"() {
        const other = env("E2E_OTHER_TENANT");
        await step("登入", signIn);
        await step("切到別的租戶：被擋下，沒有工作區，也看不到後台的外殼", async () => {
            await page.evaluate((target) => window.Blazor.navigateTo(`/portal/${target}`), other);
            await until("應顯示不能開啟", async () => (await page.locator("body").innerText()).includes("你目前不能開啟這個頁面"));
            if ((await page.locator("#query").count()) !== 0) throw new Error("別人的租戶仍出現了輸入框");
            const text = await page.locator("body").innerText();
            if (text.includes("稽核紀錄") || (await page.locator("a.ep-side-link[href='/ui']").count()) !== 0) throw new Error(`客戶看到了後台的外殼：${text}`);
        });
        await step("可以回到自己的組織", async () => {
            await page.click("a.ep-btn-primary");
            await until("應回到自己的工作區", async () => (await page.locator("#query").count()) === 1);
        });
    },
};

let exitCode = 0;
try {
    if (!scenarios[scenario]) throw new Error(`沒有這個情境：${scenario}（有 ${Object.keys(scenarios).join("、")}）`);
    await scenarios[scenario]();
    const unexpected = problems.filter((p) => !knownNoise.some((noise) => noise.test(p)));
    if (unexpected.length > 0) throw new Error(`瀏覽器出現錯誤：\n${unexpected.join("\n")}`);
} catch (error) {
    exitCode = 1;
    console.log(`FAIL ${current} :: ${error.message}`);
    if (shots) {
        await page.screenshot({ path: `${shots}/${scenario}-failed.png` }).catch(() => {});
    }
}

await browser.close();
process.exit(exitCode);
