"""標註的自動驗證（env-law-docs 02-05 §5.9、01-09 §6）：閘門、驗證與接受流程、統計校準。"""

from env_law_ontology.verification.gates import (
    DEFAULT_GATES,
    ExecutableGate,
    Gate,
    GateResult,
    NumbersGate,
    ReferencesGate,
)
from env_law_ontology.verification.pipeline import Settled, settle, verify

__all__ = [
    "DEFAULT_GATES",
    "ExecutableGate",
    "Gate",
    "GateResult",
    "NumbersGate",
    "ReferencesGate",
    "Settled",
    "settle",
    "verify",
]
