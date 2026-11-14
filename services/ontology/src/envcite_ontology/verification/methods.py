"""驗證方法與閘門的組合。"""

from collections.abc import Sequence

from envcite_ontology.verification.config import ModelSet
from envcite_ontology.verification.gates import DEFAULT_GATES, Gate
from envcite_ontology.verification.model_gates.independent import IndependentTestGate
from envcite_ontology.verification.model_gates.panel import PanelGate
from envcite_ontology.verification.model_gates.roundtrip import RoundtripGate
from envcite_ontology.verification.models import Model

ALL_METHODS = (
    "numbers",
    "references",
    "executable",
    "provisos",
    "roundtrip",
    "independent_test",
    "panel",
)


def gates_for(verifiers: Sequence[Model], allow_same_family: bool = False) -> tuple[Gate, ...]:
    """確定性閘門加模型閘門：往返用第一個驗證者，獨立測試用最後一個，評審團用全部。"""
    if not verifiers:
        return DEFAULT_GATES
    return (
        *DEFAULT_GATES,
        RoundtripGate(verifiers[0], allow_same_family),
        IndependentTestGate(verifiers[-1], allow_same_family),
        PanelGate(tuple(verifiers), allow_same_family=allow_same_family),
    )


def gates_from(models: ModelSet, allow_same_family: bool = False) -> tuple[Gate, ...]:
    """確定性閘門加模型閘門，角色（重抽、設計測試、評審團）由設定檔明定。"""
    return (
        *DEFAULT_GATES,
        RoundtripGate(models.roundtrip, allow_same_family),
        IndependentTestGate(models.independent_test, allow_same_family),
        PanelGate(models.panel, allow_same_family=allow_same_family),
    )
