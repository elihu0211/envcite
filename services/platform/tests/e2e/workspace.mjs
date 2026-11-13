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
            return { top: box(".ep-shell-top"), side: box(".ep-side"), js: document.documentElement.hasAttribute("data-js"), collapsed: document.documentElement.dataset.side ?? "" };
        });
        const sameShell = (expected, actual, where) => {
            if (!actual.js) throw new Error(`${where}：<html> 沒有 data-js`);
            if (actual.collapsed !== expected.collapsed) throw new Error(`${where}：收合狀態變了 ${JSON.stringify(expected.collapsed)} → ${JSON.stringify(actual.collapsed)}`);
            if (JSON.stringify(actual.top) !== JSON.stringify(expected.top)) throw new Error(`${where}：頂欄 ${JSON.stringify(expected.top)} → ${JSON.stringify(actual.top)}`);
            if (JSON.stringify(actual.side) !== JSON.stringify(expected.side)) throw new Error(`${where}：側欄 ${JSON.stringify(expected.side)} → ${JSON.stringify(actual.side)}`);
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
            if (expanded.top?.[0] !== 0 || expanded.top?.[1] !== 0) throw new Error(`頂欄沒有貼齊視窗的左上角：${JSON.stringify(expanded.top)}`);
            if (expanded.side?.[1] !== expanded.top[3]) throw new Error(`側欄沒有接在頂欄底下：${JSON.stringify(expanded)}`);
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
