"""本體的約定：抽取者、往返驗證的重抽者、評審團讀同一份。

三次真實跑各暴露一個同類的缺陷：約定只告訴了其中一端。抽取者被告知授權屬構成性、不寫義務方向，
往返驗證的重抽者沒被告知，於是把「得」讀成許可，整類授權條款被擋下；抽取者把「按次處罰」寫成
前一個罰鍰的屬性，評審團沒被告知本體怎麼表達它，認定是添加，所有罰則被駁回。

所以約定寫在這一處，三個提示都以它組成（`apply`），不各寫各的。不含大括號：它會被放進
`str.format` 的樣板。
"""

from __future__ import annotations

CONVENTIONS = """本體的約定（抽取、重抽與審查都依這一份）：
A. 只有 kind 為 prescriptive 才有 deontic（obligation、prohibition、permission）；constitutive
   （定義、適用範圍、授權）與 penalty（罰則）的 deontic 為 null。授權主管機關訂定或指定的「得」
   與「由…定之」是授權（constitutive），不是許可。
B. 一個罰則句常含幾個效果（罰鍰、命限期改善、按次處罰、停工停業、廢止許可）：一個效果寫成一個
   penalty 陳述，各自有自己的要件與引文；罰鍰的效果與額度屬於那個罰鍰的陳述。
C. 「屆期仍未改善者，按次處罰」是另一個 penalty 陳述：effect 為 fine、repeating 為 true，前提
   「屆期仍未完成改善」是它自己的要件；前面那個罰鍰的陳述 repeating 為 false，也不帶這個要件。
   所以標有「按次處罰」的陳述，其前提寫在它自己的要件裡，不是前一個罰則的屬性。
D. 要件的 judgement 只給構成要件的不確定法律概念（情節重大、必要時）。效果面的裁量（條文寫「得」處罰
   或「得」命令，相對於「應」）不是要件，寫在罰則的 discretionary。改善命令等預防性處分用 effect
   order，不是處罰。
"""


def apply(template: str) -> str:
    """把 `{conventions}` 換成約定；樣板其餘的大括號照舊留給之後的 `str.format`。"""
    return template.replace("{conventions}", CONVENTIONS)
