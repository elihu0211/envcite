"""統計校準：不靠人逐條審，而靠外部既有的標準答案估計驗證管線的準確度，並給有效的信賴區間。

標準答案來自**已經存在**的資料集（公開的法規推理基準、已發表的法規形式化），不是為本系統另請人標。
閘門通過率與外部標準答案之間的差，用預測驅動推論（prediction-powered inference）修正：
少量有標準答案的樣本估計「代理（閘門或模型）的平均偏差」，加到大量沒有標準答案的樣本上，得到
無偏的估計與有效的信賴區間；代理越差，區間越寬，不會變成錯的。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt
from statistics import NormalDist, fmean, variance


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float


def ppi_mean(
    truth_labeled: Sequence[float],
    proxy_labeled: Sequence[float],
    proxy_unlabeled: Sequence[float],
    alpha: float = 0.05,
) -> Estimate:
    """代理量的平均（例如驗證通過的陳述中，實際正確的比率）：預測驅動推論的平均數估計。

    `truth_labeled`、`proxy_labeled`：有標準答案的樣本上，標準答案與代理的值（同長度、同順序）。
    `proxy_unlabeled`：沒有標準答案的樣本上代理的值。
    估計 = 未標準答案樣本的代理平均 + 有標準答案樣本上（標準答案 − 代理）的平均（校正項）；
    變異 = 兩部分變異之和，信賴區間用常態近似（樣本很小時偏窄，需要時改用拔靴法）。
    """
    n, big_n = len(truth_labeled), len(proxy_unlabeled)
    if n != len(proxy_labeled):
        raise ValueError("labeled truth and proxy must have the same length")
    if n < 2 or big_n < 2:
        raise ValueError("need at least two labeled and two unlabeled samples")
    rectifier = [t - f for t, f in zip(truth_labeled, proxy_labeled, strict=True)]
    estimate = fmean(proxy_unlabeled) + fmean(rectifier)
    se = sqrt(variance(proxy_unlabeled) / big_n + variance(rectifier) / n)
    z = NormalDist().inv_cdf(1 - alpha / 2)
    return Estimate(estimate, estimate - z * se, estimate + z * se)
