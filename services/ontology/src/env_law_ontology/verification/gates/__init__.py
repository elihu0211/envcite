"""確定性驗證閘門（env-law-docs 02-05 §5.9）：檢查標註與原文、與推論程式是否一致，不呼叫模型。

- `numbers`：罰則額度與帶單位的門檻值與原文數字一致。
- `references`：罰則所處罰的陳述在原文引用的條（項）內。
- `executable`：結論隨義務方向翻轉；罰則僅在所處罰陳述違反且自身適用範圍成立時成為候選。
- `provisos`：原文有但書，同條內就要有承接它的例外陳述。

語意、種類、義務方向是否忠於原文須由模型閘門（`model_gates`）判定。
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
