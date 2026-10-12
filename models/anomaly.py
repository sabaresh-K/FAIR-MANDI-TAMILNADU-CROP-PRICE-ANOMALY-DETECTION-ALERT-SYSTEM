"""
Anomaly Detection Module: anomaly.py
Combines Z-Score, Interquartile Range (IQR), and Isolation Forest ML models
to compute a unified anomaly score and boolean anomaly flag per crop and market.
"""

import logging
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from config.config import (
    DEFAULT_ANOMALY_THRESHOLD,
    CROP_ANOMALY_THRESHOLDS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def compute_statistical_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes Z-score, day-over-day % change, and IQR outlier flags on residual.
    """
    df_out = df.copy()

    # Calculate day-over-day % change per (market, commodity)
    df_out = df_out.sort_values(["market", "commodity", "date"]).reset_index(drop=True)
    df_out["prev_price"] = df_out.groupby(["market", "commodity"])["modal_price"].shift(1)
    
    df_out["pct_change_dod"] = (
        (df_out["modal_price"] - df_out["prev_price"]) / df_out["prev_price"] * 100.0
    ).fillna(0.0)

    # Compute Z-score and IQR bounds of residual per (market, commodity) using vectorized transform
    mean_res = df_out.groupby(["market", "commodity"])["residual"].transform("mean")
    std_res = df_out.groupby(["market", "commodity"])["residual"].transform("std").fillna(0.0)

    df_out["z_score"] = np.where(std_res > 1e-6, (df_out["residual"] - mean_res) / std_res, 0.0)

    q1 = df_out.groupby(["market", "commodity"])["residual"].transform(lambda x: x.quantile(0.25))
    q3 = df_out.groupby(["market", "commodity"])["residual"].transform(lambda x: x.quantile(0.75))
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    df_out["iqr_outlier"] = np.where(iqr > 1e-6, (df_out["residual"] < lower_bound) | (df_out["residual"] > upper_bound), False)

    df_out.drop(columns=["prev_price"], errors="ignore", inplace=True)
    return df_out


def fit_isolation_forest(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fits Isolation Forest per commodity on features: [residual, pct_change_dod, arrivals_tonnes].
    Returns DataFrame with 'iforest_score' normalized to [0, 1] where higher = more anomalous.
    """
    df_out = df.copy()
    df_out["iforest_score"] = 0.0

    if df_out.empty:
        return df_out

    feature_cols = ["residual", "pct_change_dod", "arrivals_tonnes"]

    for commodity, group in df_out.groupby("commodity"):
        if len(group) < 5:
            # Not enough samples to fit isolation forest reliably
            df_out.loc[group.index, "iforest_score"] = 0.0
            continue

        X = group[feature_cols].fillna(0.0).values
        
        # Add small jitter if all values are identical to avoid zero variance issues
        if np.std(X) < 1e-6:
            X = X + np.random.normal(0, 1e-5, X.shape)

        try:
            clf = IsolationForest(
                n_estimators=100,
                contamination=0.05,
                random_state=42,
            )
            clf.fit(X)
            
            # decision_function returns negative for anomalies, positive for inliers
            raw_scores = clf.decision_function(X)
            
            # Invert and normalize to [0, 1]: higher means more anomalous
            min_s, max_s = raw_scores.min(), raw_scores.max()
            if max_s - min_s > 1e-6:
                norm_scores = 1.0 - (raw_scores - min_s) / (max_s - min_s)
            else:
                norm_scores = np.zeros_like(raw_scores)

            df_out.loc[group.index, "iforest_score"] = norm_scores

        except Exception as e:
            logger.warning(f"Isolation Forest fitting failed for commodity {commodity}: {e}")
            df_out.loc[group.index, "iforest_score"] = 0.0

    return df_out


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full anomaly detection pipeline:
    1. Computes Z-score and IQR outliers.
    2. Fits Isolation Forest.
    3. Combines into composite anomaly_score and flags is_anomaly based on crop thresholds.
    """
    if df.empty:
        return df

    # Step 1: Statistical features
    df_stat = compute_statistical_outliers(df)

    # Step 2: ML Isolation Forest
    df_ml = fit_isolation_forest(df_stat)

    # Step 3: Composite Score & Crop Thresholding
    df_ml["z_score_norm"] = np.minimum(np.abs(df_ml["z_score"]) / 3.0, 1.0)
    df_ml["iqr_score"] = df_ml["iqr_outlier"].astype(float)

    # Weighted composite score
    df_ml["anomaly_score"] = (
        0.40 * df_ml["z_score_norm"] +
        0.35 * df_ml["iforest_score"] +
        0.25 * df_ml["iqr_score"]
    )

    def is_crop_anomaly(row):
        comm = str(row["commodity"]).lower()
        threshold = CROP_ANOMALY_THRESHOLDS.get(comm, DEFAULT_ANOMALY_THRESHOLD)
        return bool(row["anomaly_score"] >= threshold)

    df_ml["is_anomaly"] = df_ml.apply(is_crop_anomaly, axis=1)

    # Clean up temporary columns
    df_ml.drop(columns=["z_score_norm", "iqr_score"], errors="ignore", inplace=True)

    return df_ml
