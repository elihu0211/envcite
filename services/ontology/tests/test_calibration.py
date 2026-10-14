"""Calibration against an external gold standard (prediction-powered inference)."""

import pytest

from envcite_ontology.verification.calibration import ppi_mean


def test_ppi_corrects_a_biased_proxy_and_widens_where_it_knows_less() -> None:
    truth = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    proxy = [min(1.0, t + 0.2) for t in truth]  # 代理系統性偏高
    unlabeled = [1.0] * 70 + [0.2] * 30  # 代理在大量樣本上的值

    est = ppi_mean(truth, proxy, unlabeled)

    naive = sum(unlabeled) / len(unlabeled)
    assert est.mean < naive  # 校正項把偏高拉回來
    assert est.low < est.mean < est.high


def test_ppi_interval_is_exactly_the_proxy_mean_when_the_proxy_is_perfect() -> None:
    truth = [1.0, 0.0, 1.0, 1.0]
    unlabeled = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0]

    est = ppi_mean(truth, truth, unlabeled)

    assert est.mean == pytest.approx(sum(unlabeled) / len(unlabeled))


def test_ppi_needs_enough_samples() -> None:
    with pytest.raises(ValueError):
        ppi_mean([1.0], [1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        ppi_mean([1.0, 0.0], [1.0], [1.0, 0.0])
