"""規範推理：從資料庫的標註到候選結論（envcite-docs 02-06 §5）。"""

from envcite_ontology.reasoning.engine import Penalty, Result, Trace, evaluate
from envcite_ontology.reasoning.transition import Candidate, Transition, evaluate_across

__all__ = ["Candidate", "Penalty", "Result", "Trace", "Transition", "evaluate", "evaluate_across"]
