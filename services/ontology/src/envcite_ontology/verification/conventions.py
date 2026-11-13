"""本體的約定：抽取者、往返重抽者、評審團讀同一份，避免約定只告訴其中一端而誤判。

三個提示都以 `apply` 組成。不得含大括號：會被放進 `str.format` 的樣板。
"""

CONVENTIONS = """本體的約定（抽取、重抽與審查都依這一份）：
A. 只有 kind 為 prescriptive 才有 deontic（obligation、prohibition、permission）；constitutive
   （定義、適用範圍、授權）與 sanction（罰則與改善命令等不利效果）的 deontic 為 null。
   授權主管機關訂定或指定的「得」與「由…定之」是授權（constitutive），不是許可。
B. 一個罰則句常含幾個效果（罰鍰、命限期改善、按次處罰、停工停業、廢止許可）：一個效果寫成一個
   sanction 陳述，各自有自己的要件與引文；罰鍰的效果與額度屬於那個罰鍰的陳述。
   每個陳述的引文只取自己效果的子句，不含其他效果：「…者，處新臺幣…罰鍰，並通知限期改善，屆期仍未
   完成改善者，按日連續處罰；情節重大者，禁止供飲用」是四個陳述，引文依序是「…者，處新臺幣…罰鍰」
   （fine）、「通知限期改善」（order）、「屆期仍未完成改善者，按日連續處罰」（fine、repeating）、
   「情節重大者，禁止供飲用」（restriction）。
C. 「屆期仍未改善者，按次處罰」是另一個 sanction 陳述：effect 為 fine、repeating 為 true，前提
   「屆期仍未完成改善」是它自己的要件；前面那個罰鍰的陳述 repeating 為 false，也不帶這個要件。
   所以標有「按次處罰」的陳述，其前提寫在它自己的要件裡，不是前一個罰則的屬性。它以同一句的
   限期改善（effect order）為前提：triggers 指向那個 order 陳述，kind 為 order_lapsed。
D. 要件的 judgement 只給構成要件的不確定法律概念（情節重大、必要時）。效果面的裁量（條文寫「得」處罰
   或「得」命令，相對於「應」）不是要件，寫在罰則的 discretionary。改善命令等預防性處分用 effect
   order，不是處罰。
E. 追繳違反義務所得的利益用 effect disgorgement，不是處罰也不是罰鍰；環境講習是警告性處分，用 effect
   warning。
   以另一處分已作成為前提的效果（處停工、停業或一定金額以上罰鍰者，另令接受講習），triggers 指向
   那個處分的陳述，kind 為 sanction_imposed；金額門檻是這個效果自己的要件。
F. 授權命令（排放標準、管制標準）訂定的數值限值是 constitutive 陳述、role 為 limit 的要件，
   不是義務；檢測方法與平均時段是限值的認定方式，只屬 limit。
G. 要件的 concept 是這句話說的那件事，用引文自己的字命名、在 concepts 宣告，標籤寫成命題。義務的
   duty 是被要求做到的狀態或行為（「放流水符合放流水標準」「污泥已妥善處理」）；禁止的 duty 是
   被禁止的那件事（「放流水超過水體涵容能力」，成立即違反），不是數值。scope 是「者」之前的情形，
   引文沒寫的條件不寫。本法定義的用語（提示有清單時）只用於 value_concept
   （判斷主體或對象屬不屬於該用語），不得借來當 duty 或 scope 的概念：
   「水污染防治措施」「污染物」這類泛稱不是這個義務的內容。
H. 刑罰（有期徒刑、拘役、罰金）不是行政罰，沒有對應的 effect：罰金不是 fine。上層條文處的是刑罰
   時，其下的款、目不寫成 sanction 陳述。
"""


def apply(template: str) -> str:
    """把 `{conventions}` 換成約定；樣板其餘的大括號照舊留給之後的 `str.format`。"""
    return template.replace("{conventions}", CONVENTIONS)
