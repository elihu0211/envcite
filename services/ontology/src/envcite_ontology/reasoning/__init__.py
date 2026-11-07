"""規範推理：從資料庫的標註到候選結論。"""

from envcite_ontology.reasoning.engine import Penalty, Result, Trace, evaluate
from envcite_ontology.reasoning.subsumption import (
    CaseResult,
    SubsumptionCase,
    SubsumptionReport,
    evaluate_subsumption,
)
from envcite_ontology.reasoning.transition import Candidate, Transition, evaluate_across

__all__ = [
    "Candidate",
    "CaseResult",
    "Penalty",
    "Result",
    "SubsumptionCase",
    "SubsumptionReport",
    "Trace",
    "Transition",
    "evaluate",
    "evaluate_across",
    "evaluate_subsumption",
]
