"""
Decomposition Module: decompose.py
Performs STL (Seasonal and Trend decomposition using Loess) on commodity modal prices.
Handles sparse or short time series gracefully by falling back to rolling mean statistics.
"""

import logging
from typing import Dict, Tuple
import pandas as pd
import numpy as np

try:
    from statsmodels.tsa.seasonal import STL
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

from config.config import STL_PERIOD_DAYS, MIN_HISTORY_POINTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def decompose_series(
    df_series: pd.DataFrame,
    period: int = STL_PERIOD_DAYS,
    min_points: int = MIN_HISTORY_POINTS,
) -> pd.DataFrame:
    """
    Decomposes modal_price for a single (market, commodity) DataFrame.
    Expects df_series to contain 'date' (YYYY-MM-DD or datetime) and 'modal_price'.
    Returns DataFrame with added columns: ['trend', 'seasonal', 'residual'].
    """
    df_out = df_series.copy()
    df_out["date"] = pd.to_datetime(df_out["date"])
    df_out = df_out.sort_values("date").reset_index(drop=True)

    n_obs = len(df_out)

    # Initialize default fallback columns
    df_out["trend"] = df_out["modal_price"]
    df_out["seasonal"] = 0.0
    df_out["residual"] = 0.0

    if n_obs < min_points or not HAS_STATSMODELS:
        # Fallback to rolling statistics for short history
        logger.debug(f"Series length {n_obs} < min_points {min_points}. Using rolling fallback.")
        window = min(7, max(3, n_obs))
        df_out["trend"] = df_out["modal_price"].rolling(window=window, min_periods=1).mean()
        df_out["seasonal"] = 0.0
        df_out["residual"] = df_out["modal_price"] - df_out["trend"]
        df_out["date"] = df_out["date"].dt.strftime("%Y-%m-%d")
        return df_out

    try:
        # Set date as index to form a complete daily time series
        full_idx = pd.date_range(start=df_out["date"].min(), end=df_out["date"].max(), freq="D")
        ts_df = df_out.set_index("date").reindex(full_idx)

        # Linearly interpolate missing dates in modal_price for continuous STL
        ts_df["modal_price_interp"] = ts_df["modal_price"].interpolate(method="linear").bfill().ffill()

        # Adjust STL period if series is shorter than 2 * period
        stl_period = period
        if len(ts_df) < 2 * stl_period:
            stl_period = max(3, len(ts_df) // 2)

        # Run STL decomposition
        stl = STL(ts_df["modal_price_interp"], period=stl_period, robust=True)
        res = stl.fit()

        ts_df["trend"] = res.trend
        ts_df["seasonal"] = res.seasonal
        ts_df["residual"] = res.resid

        # Re-align back to original observation dates
        merged = df_out[["date"]].merge(
            ts_df[["trend", "seasonal", "residual"]],
            left_on="date",
            right_index=True,
            how="left",
        )

        df_out["trend"] = merged["trend"].values
        df_out["seasonal"] = merged["seasonal"].values
        df_out["residual"] = merged["residual"].values

    except Exception as e:
        logger.warning(f"STL decomposition failed: {e}. Falling back to rolling mean.")
        df_out["trend"] = df_out["modal_price"].rolling(window=7, min_periods=1).mean()
        df_out["seasonal"] = 0.0
        df_out["residual"] = df_out["modal_price"] - df_out["trend"]

    df_out["date"] = df_out["date"].dt.strftime("%Y-%m-%d")
    return df_out


def run_decomposition_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Runs STL decomposition across all (market, commodity) groups in the DataFrame.
    """
    if df.empty:
        return df

    decomposed_groups = []
    for (market, commodity), group in df.groupby(["market", "commodity"]):
        group_decomposed = decompose_series(group)
        decomposed_groups.append(group_decomposed)

    if decomposed_groups:
        result_df = pd.concat(decomposed_groups, ignore_index=True)
        return result_df.sort_values(["commodity", "market", "date"]).reset_index(drop=True)
    
    return df
