"""環境管制物理量測與法規門檻的單位換算及量綱相容性檢查。

推論前必須先核對案件事實的單位與條文／限值的標準單位是否屬同一物理量綱：
  - 相容單位（例如 `ug/L` 與 `mg/L`、`ppb` 與 `ppm`、`t` 與 `kg`）換算至目標單位後比較；
  - 不相容單位（例如 `mg/L` 與 `dB(A)`、`mg/Nm3` 與 `mg/L`）不得靜默比較，
    由推論機回報 `problem("unit_mismatch", concept)`。
"""

from decimal import Decimal

#: (物理量綱, 相對基準單位的乘數)
_UNIT_SPEC: dict[str, tuple[str, Decimal]] = {
    # 液體／水質質量濃度（基準：mg/L）
    "mg/L": ("mass_conc_liquid", Decimal("1")),
    "ug/L": ("mass_conc_liquid", Decimal("0.001")),
    "µg/L": ("mass_conc_liquid", Decimal("0.001")),
    "ng/L": ("mass_conc_liquid", Decimal("0.000001")),
    "g/L": ("mass_conc_liquid", Decimal("1000")),
    "kg/L": ("mass_conc_liquid", Decimal("1000000")),
    # 氣體／體積混和比（基準：ppm）
    "ppm": ("volume_ratio", Decimal("1")),
    "ppb": ("volume_ratio", Decimal("0.001")),
    "%": ("volume_ratio", Decimal("10000")),
    # 氣體／標準狀態質量濃度（基準：mg/Nm3）
    "mg/Nm3": ("mass_conc_gas", Decimal("1")),
    "ug/Nm3": ("mass_conc_gas", Decimal("0.001")),
    "µg/Nm3": ("mass_conc_gas", Decimal("0.001")),
    "g/Nm3": ("mass_conc_gas", Decimal("1000")),
    "mg/m3": ("mass_conc_gas", Decimal("1")),
    "ug/m3": ("mass_conc_gas", Decimal("0.001")),
    "µg/m3": ("mass_conc_gas", Decimal("0.001")),
    # 質量（基準：kg）
    "kg": ("mass", Decimal("1")),
    "g": ("mass", Decimal("0.001")),
    "mg": ("mass", Decimal("0.000001")),
    "t": ("mass", Decimal("1000")),
    "ton": ("mass", Decimal("1000")),
    "公噸": ("mass", Decimal("1000")),
    # 每日排放水量／體積流量（基準：m3/day）
    "m3/day": ("flow_daily", Decimal("1")),
    "CMD": ("flow_daily", Decimal("1")),
    "L/day": ("flow_daily", Decimal("0.001")),
    # 噪音音量（基準：dB(A)）
    "dB(A)": ("sound_level", Decimal("1")),
    "dBA": ("sound_level", Decimal("1")),
    "dB": ("sound_level", Decimal("1")),
    # 貨幣（基準：NTD）
    "NTD": ("currency", Decimal("1")),
    "元": ("currency", Decimal("1")),
    # 時間（基準：hour）
    "hour": ("time", Decimal("1")),
    "hr": ("time", Decimal("1")),
    "小時": ("time", Decimal("1")),
    "day": ("time", Decimal("24")),
    "日": ("time", Decimal("24")),
    # 無因次點數／倍數
    "point": ("dimensionless", Decimal("1")),
    "點": ("dimensionless", Decimal("1")),
    "ratio": ("dimensionless", Decimal("1")),
}


def canonical_unit_name(unit: str | None) -> str | None:
    """去除空白並正規化常見別名；未提供回 None。"""
    if unit is None:
        return None
    cleaned = unit.strip()
    return cleaned or None


def normalize_value(
    value: Decimal | float | int,
    from_unit: str | None,
    to_unit: str | None,
) -> tuple[Decimal | None, bool]:
    """將數值從 `from_unit` 換算為 `to_unit`。

    兩端任一未標註或字串完全相同時，視為相容並原樣回傳 Decimal。
    兩端皆已標註但分屬不同物理量綱或未知單位組合時，回傳 `(None, False)`。
    """
    src = canonical_unit_name(from_unit)
    dst = canonical_unit_name(to_unit)
    dec = Decimal(str(value))
    if src is None or dst is None or src == dst:
        return dec, True
    src_spec = _UNIT_SPEC.get(src)
    dst_spec = _UNIT_SPEC.get(dst)
    if src_spec is None or dst_spec is None or src_spec[0] != dst_spec[0]:
        return None, False
    return (dec * src_spec[1]) / dst_spec[1], True
