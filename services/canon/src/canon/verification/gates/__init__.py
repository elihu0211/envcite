"""確定性驗證閘門：檢查標註與原文、與推論程式是否一致，不呼叫模型。

- `numbers`：罰則額度與帶單位的門檻值與原文數字一致。
- `references`：罰則所處罰的陳述在原文引用的條（項）內。
- `executable`：結論隨義務方向翻轉；罰則僅在所處罰陳述違反且自身適用範圍成立時成為候選。
- `provisos`：原文有但書，同條內就要有承接它的例外陳述。

語意、種類、義務方向是否忠於原文須由模型閘門（`model_gates`）判定。
"""

from canon.verification.gates.base import Gate, GateResult, Unit, Verdict, unit_of
from canon.verification.gates.cn_number import NUMBER, amounts, cn_number, numbers_in
from canon.verification.gates.conditions import statement_as_of
from canon.verification.gates.executable_gate import ExecutableGate
from canon.verification.gates.numbers_gate import NumbersGate
from canon.verification.gates.proviso_gate import ProvisoGate
from canon.verification.gates.references_gate import ReferencesGate

DEFAULT_GATES: tuple[Gate, ...] = (NumbersGate(), ReferencesGate(), ExecutableGate(), ProvisoGate())

__all__ = [
    "DEFAULT_GATES",
    "NUMBER",
    "ExecutableGate",
    "Gate",
    "GateResult",
    "NumbersGate",
    "ProvisoGate",
    "ReferencesGate",
    "Unit",
    "Verdict",
    "amounts",
    "cn_number",
    "numbers_in",
    "statement_as_of",
    "unit_of",
]
