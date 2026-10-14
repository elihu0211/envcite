"""標註的自動驗證：閘門、驗證與接受流程、統計校準。"""

from envcite_ontology.verification.gates import (
    DEFAULT_GATES,
    ExecutableGate,
    Gate,
    GateResult,
    NumbersGate,
    ProvisoGate,
    ReferencesGate,
)
from envcite_ontology.verification.model_gates import IndependentTestGate, PanelGate, RoundtripGate
from envcite_ontology.verification.models import (
    AnthropicModel,
    ChatCompletionsModel,
    Model,
    ModelError,
)
from envcite_ontology.verification.pipeline import Settled, settle, verify

__all__ = [
    "DEFAULT_GATES",
    "AnthropicModel",
    "ChatCompletionsModel",
    "ExecutableGate",
    "Gate",
    "GateResult",
    "IndependentTestGate",
    "Model",
    "ModelError",
    "NumbersGate",
    "PanelGate",
    "ProvisoGate",
    "ReferencesGate",
    "RoundtripGate",
    "Settled",
    "settle",
    "verify",
]
