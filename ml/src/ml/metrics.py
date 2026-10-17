"""AUC, Gini, KS and calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve


def ks_statistic(y_true, score) -> float:
    """Max distance between the score CDFs of defaults and non-defaults, i.e. max(TPR - FPR)."""
    fpr, tpr, _ = roc_curve(y_true, score)
    return float(np.max(tpr - fpr))


def evaluate(y_true, pd_hat) -> dict[str, float]:
    y_true = np.asarray(y_true)
    pd_hat = np.asarray(pd_hat)
    auc = float(roc_auc_score(y_true, pd_hat))
    return {
        "n": int(len(y_true)),
        "defaults": int(y_true.sum()),
        "auc": auc,
        "gini": 2 * auc - 1,
        "ks": ks_statistic(y_true, pd_hat),
        "brier": float(brier_score_loss(y_true, pd_hat)),
        "mean_pd": float(pd_hat.mean()),
        "observed_default_rate": float(y_true.mean()),
    }


def calibration_table(y_true, pd_hat, bins: int = 10) -> pd.DataFrame:
    """Mean predicted PD vs observed default rate per predicted-PD quantile bin."""
    frame = pd.DataFrame({"y": np.asarray(y_true), "pd": np.asarray(pd_hat)})
    frame["bin"] = pd.qcut(frame["pd"].rank(method="first"), q=bins, labels=False) + 1
    table = frame.groupby("bin").agg(n=("y", "size"), mean_pd=("pd", "mean"), observed_rate=("y", "mean"))
    return table.reset_index()
