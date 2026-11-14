"""標註文件裡可寫的列舉值：與資料庫的同名列舉一致，抽取器的提示與解碼期 Schema 由這裡組成。"""

#: 文件裡可寫的列舉值，與資料庫的同名列舉一致（`tests/test_environmental_annotation.py` 守門）；
#: 抽取器的提示與解碼期 Schema 由這裡組成。
LEGAL_EFFECTS = (
    "fine",
    "confiscation",
    "restriction",
    "deprivation",
    "reputation",
    "warning",
    "order",
    "disgorgement",
)


LIABILITY_BASES = ("act", "state", "representative")


TRIGGER_KINDS = ("order_lapsed", "sanction_imposed")
