"""統計校準：以外部既有的標準答案，用預測驅動推論（PPI）估計驗證管線的準確度與信賴區間。

標準答案取自既有資料集，不為本系統另請人標；代理越差區間越寬，不會變成錯的。
"""

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
    """PPI 平均數估計：無標準答案樣本的代理平均，加有標準答案樣本上（標準答案 − 代理）的平均。

    標準答案與代理須同長同序。信賴區間用常態近似，樣本很小時偏窄。
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
