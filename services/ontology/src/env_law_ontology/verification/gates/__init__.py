"""驗證閘門：每個閘門檢查標註的一個性質，回報通過、失敗、無法判定，或不適用。

閘門檢查的是「標註與原文、與推論程式是否一致」，不是「法律上對不對」。這裡的四個都是確定性的
工具，不呼叫語言模型：

- `numbers`：罰則的額度與帶單位的門檻值，必須與原文的數字一致（中文數字與阿拉伯數字都讀）。
- `references`：罰則所處罰的陳述，必須是原文引用的那一條（項）。
- `executable`：規範性陳述的結論隨義務方向翻轉；罰則只在所處罰的陳述已違反、
  且自己的適用範圍成立時成為候選。
- `provisos`：原文有但書，同一條裡就要有承接它的例外陳述；往返驗證看不到原文，漏掉的但書
  會在那裡一起消失。

它們互相獨立，也獨立於抽取者；但沒有一個能證明要件的語意、陳述的種類或義務方向忠於原文——那需要
往返驗證、獨立測試或跨家族評審（設計見 02-05 §5.9），它們要呼叫模型，不在這裡。

一個閘門一個模組：`numbers_gate`、`references_gate`、`executable_gate`；共用的是 `base`（型別與
讀取陳述所在的單元）、`cn_number`（讀數字）與 `conditions`（可執行閘門讀的要件）。
"""

from env_law_ontology.verification.gates.base import Conn, Gate, GateResult, Unit, Verdict, unit_of
from env_law_ontology.verification.gates.cn_number import NUMBER, amounts, cn_number, numbers_in
from env_law_ontology.verification.gates.conditions import statement_as_of
from env_law_ontology.verification.gates.executable_gate import ExecutableGate
from env_law_ontology.verification.gates.numbers_gate import NumbersGate
from env_law_ontology.verification.gates.proviso_gate import ProvisoGate
from env_law_ontology.verification.gates.references_gate import ReferencesGate, citations

DEFAULT_GATES: tuple[Gate, ...] = (NumbersGate(), ReferencesGate(), ExecutableGate(), ProvisoGate())

__all__ = [
    "DEFAULT_GATES",
    "NUMBER",
    "Conn",
    "ExecutableGate",
    "Gate",
    "GateResult",
    "NumbersGate",
    "ProvisoGate",
    "ReferencesGate",
    "Unit",
    "Verdict",
    "amounts",
    "citations",
    "cn_number",
    "numbers_in",
    "statement_as_of",
    "unit_of",
]
