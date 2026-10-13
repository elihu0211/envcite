"""規範推理：從資料庫的標註到候選結論（env-law-docs 02-06 §5）。"""

from env_law_ontology.reasoning.engine import Penalty, Result, Trace, evaluate
from env_law_ontology.reasoning.transition import Candidate, Transition, evaluate_across

__all__ = ["Candidate", "Penalty", "Result", "Trace", "Transition", "evaluate", "evaluate_across"]
