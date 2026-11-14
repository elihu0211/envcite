"""領域型別的列舉到 protobuf 列舉的對照表。

資料庫與推論程式的字串若出現表中沒有的值，是 `KeyError`（內部錯誤），不悄悄落到 UNSPECIFIED。"""

from envcite.ontology.v2 import reasoning_pb2 as pb

STATEMENT_KIND = {
    "prescriptive": pb.STATEMENT_KIND_PRESCRIPTIVE,
    "constitutive": pb.STATEMENT_KIND_CONSTITUTIVE,
    "sanction": pb.STATEMENT_KIND_PENALTY,
    "reparation": pb.STATEMENT_KIND_REPARATION,
}

NORM_MODALITY = {
    "obligation": pb.NORM_MODALITY_OBLIGATION,
    "prohibition": pb.NORM_MODALITY_PROHIBITION,
    "permission": pb.NORM_MODALITY_PERMISSION,
}

SLOT_ROLE = {
    "subject": pb.SLOT_ROLE_SUBJECT,
    "action": pb.SLOT_ROLE_ACTION,
    "object": pb.SLOT_ROLE_OBJECT,
    "medium": pb.SLOT_ROLE_MEDIUM,
    "time": pb.SLOT_ROLE_TIME,
    "place": pb.SLOT_ROLE_PLACE,
}

COMPARATOR = {
    "le": pb.COMPARATOR_LE,
    "lt": pb.COMPARATOR_LT,
    "ge": pb.COMPARATOR_GE,
    "gt": pb.COMPARATOR_GT,
    "eq": pb.COMPARATOR_EQ,
    "ne": pb.COMPARATOR_NE,
}

CONDITION_ROLE = {
    "scope": pb.CONDITION_ROLE_SCOPE,
    "duty": pb.CONDITION_ROLE_DUTY,
    "limit": pb.CONDITION_ROLE_LIMIT,
}

LEGAL_EFFECT = {
    "fine": pb.LEGAL_EFFECT_FINE,
    "confiscation": pb.LEGAL_EFFECT_CONFISCATION,
    "restriction": pb.LEGAL_EFFECT_RESTRICTION,
    "deprivation": pb.LEGAL_EFFECT_DEPRIVATION,
    "reputation": pb.LEGAL_EFFECT_REPUTATION,
    "warning": pb.LEGAL_EFFECT_WARNING,
    "order": pb.LEGAL_EFFECT_ORDER,
    "disgorgement": pb.LEGAL_EFFECT_DISGORGEMENT,
}

LIABILITY_BASIS = {
    "act": pb.LIABILITY_BASIS_ACT,
    "state": pb.LIABILITY_BASIS_STATE,
    "representative": pb.LIABILITY_BASIS_REPRESENTATIVE,
}

TRIGGER_KIND = {
    "order_lapsed": pb.TRIGGER_KIND_ORDER_LAPSED,
    "sanction_imposed": pb.TRIGGER_KIND_SANCTION_IMPOSED,
}

GUIDANCE_KIND = {
    "interpretation": pb.GUIDANCE_KIND_INTERPRETATION,
    "discretion_standard": pb.GUIDANCE_KIND_DISCRETION_STANDARD,
    "operational": pb.GUIDANCE_KIND_OPERATIONAL,
}

OVERRIDE_BASIS = {
    "hierarchy": pb.OVERRIDE_BASIS_HIERARCHY,
    "lex_specialis": pb.OVERRIDE_BASIS_LEX_SPECIALIS,
    "lex_posterior": pb.OVERRIDE_BASIS_LEX_POSTERIOR,
    "exception": pb.OVERRIDE_BASIS_EXCEPTION,
    "mutatis_mutandis": pb.OVERRIDE_BASIS_MUTATIS_MUTANDIS,
    "subsidiarity": pb.OVERRIDE_BASIS_SUBSIDIARITY,
    "absorption": pb.OVERRIDE_BASIS_ABSORPTION,
}

VERDICT = {
    "violation": pb.VERDICT_VIOLATION,
    "compliant": pb.VERDICT_COMPLIANT,
    "conditional_violation": pb.VERDICT_CONDITIONAL_VIOLATION,
    "conditional_compliant": pb.VERDICT_CONDITIONAL_COMPLIANT,
    "undetermined": pb.VERDICT_UNDETERMINED,
    "excluded": pb.VERDICT_EXCLUDED,
    "not_applicable": pb.VERDICT_NOT_APPLICABLE,
}

EXCUSE = {
    "no_fault": pb.EXCUSE_NO_FAULT,
    "under_fourteen": pb.EXCUSE_UNDER_FOURTEEN,
    "incapable": pb.EXCUSE_INCAPABLE,
    "pursuant_to_law": pb.EXCUSE_PURSUANT_TO_LAW,
    "superior_order": pb.EXCUSE_SUPERIOR_ORDER,
    "self_defense": pb.EXCUSE_SELF_DEFENSE,
    "necessity": pb.EXCUSE_NECESSITY,
    "criminal_priority": pb.EXCUSE_CRIMINAL_PRIORITY,
    "unavoidable_ignorance": pb.EXCUSE_UNAVOIDABLE_IGNORANCE,
    "unexpectable": pb.EXCUSE_UNEXPECTABLE,
    "petty_offense": pb.EXCUSE_PETTY_OFFENSE,
    "within_cure_period": pb.EXCUSE_WITHIN_CURE_PERIOD,
}

MITIGATION = {
    "youth": pb.MITIGATION_YOUTH,
    "reduced_capacity": pb.MITIGATION_REDUCED_CAPACITY,
    "ignorance": pb.MITIGATION_IGNORANCE,
    "excessive_defense": pb.MITIGATION_EXCESSIVE_DEFENSE,
    "excessive_necessity": pb.MITIGATION_EXCESSIVE_NECESSITY,
}

REDUCTION_SHARE = {2: pb.REDUCTION_SHARE_HALF, 3: pb.REDUCTION_SHARE_THIRD}

DELEGATION_GAP = {
    None: pb.DELEGATION_GAP_UNSPECIFIED,
    "no_citation": pb.DELEGATION_GAP_NO_CITATION,
    "unresolved_law": pb.DELEGATION_GAP_UNRESOLVED_LAW,
    "law_not_in_force": pb.DELEGATION_GAP_LAW_NOT_IN_FORCE,
    "law_invalidated": pb.DELEGATION_GAP_LAW_INVALIDATED,
    "cycle": pb.DELEGATION_GAP_CYCLE,
}

PROBLEM_KIND = {
    "contradictory_fact": pb.PROBLEM_KIND_CONTRADICTORY_FACT,
    "unresolved_delegation": pb.PROBLEM_KIND_UNRESOLVED_DELEGATION,
    "bad_date": pb.PROBLEM_KIND_BAD_DATE,
    "measurement_mismatch": pb.PROBLEM_KIND_MEASUREMENT_MISMATCH,
    "measurement_unlabeled": pb.PROBLEM_KIND_MEASUREMENT_UNLABELED,
    "invalidated_delegation": pb.PROBLEM_KIND_INVALIDATED_DELEGATION,
}
