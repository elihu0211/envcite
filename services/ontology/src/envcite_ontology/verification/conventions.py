"""本體的約定：抽取者、往返重抽者、評審團讀同一份，避免約定只告訴其中一端而誤判。

三個提示都以 `apply` 組成。不得含大括號：會被放進 `str.format` 的樣板。
"""

from __future__ import annotations

CONVENTIONS = """本體的約定（抽取、重抽與審查都依這一份）：
A. 只有 kind 為 prescriptive 才有 deontic（obligation、prohibition、permission）；constitutive
   （定義、適用範圍、授權）與 sanction（罰則與改善命令等不利效果）的 deontic 為 null。
   授權主管機關訂定或指定的「得」與「由…定之」是授權（constitutive），不是許可。
B. 一個罰則句常含幾個效果（罰鍰、命限期改善、按次處罰、停工停業、廢止許可）：一個效果寫成一個
   sanction 陳述，各自有自己的要件與引文；罰鍰的效果與額度屬於那個罰鍰的陳述。
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
"""


def apply(template: str) -> str:
    """把 `{conventions}` 換成約定；樣板其餘的大括號照舊留給之後的 `str.format`。"""
    return template.replace("{conventions}", CONVENTIONS)
