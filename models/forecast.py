"""
Forecasting Module: forecast.py
Generates short-term 7-day forward-looking price projections for a (commodity, market) pair
using an autoregressive model with weekly seasonal factors and historical volatility bands.
"""

import math
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def generate_7day_forecast(df_series: pd.DataFrame, days_ahead: int = 7) -> List[Dict[str, Any]]:
    """
    Produces a 7-day price forecast with trend direction, expected price, and confidence bounds.
    Expects df_series sorted by date with 'date' and 'modal_price'.
    """
    if df_series.empty or len(df_series) < 3:
        return []

    df = df_series.sort_values("date").reset_index(drop=True)
    prices = df["modal_price"].values
    last_price = float(prices[-1])
    last_date = pd.to_datetime(df["date"].iloc[-1])

    # Compute short-term momentum (last 7 days vs previous 7 days)
    if len(prices) >= 7:
        recent_window = prices[-7:]
        momentum = float(np.mean(np.diff(recent_window)))
        volatility = float(np.std(recent_window))
    else:
        momentum = float(prices[-1] - prices[0]) / max(len(prices) - 1, 1)
        volatility = float(np.std(prices)) if len(prices) > 1 else last_price * 0.05

    # Bound momentum so forecasts don't diverge wildly
    max_drift = last_price * 0.03  # Max 3% daily trend drift
    momentum = max(-max_drift, min(momentum, max_drift))

    forecast_results = []
    curr_price = last_price

    for day in range(1, days_ahead + 1):
        target_date = last_date + timedelta(days=day)
        day_of_week = target_date.weekday()

        # Weekly agricultural cycle seasonality (weekend / mandi trading day variations)
        weekly_seasonal_factor = math.sin(day_of_week / 7.0 * 2 * math.pi) * (volatility * 0.4)

        # AR(1) mean-reverting projection with slight momentum decay
        decay = 0.90 ** day
        curr_price = curr_price + (momentum * decay) + weekly_seasonal_factor

        # Ensure price does not become negative or absurdly low
        curr_price = max(curr_price, last_price * 0.4)

        lower_bound = curr_price - (1.2 * volatility * math.sqrt(day / 3.0))
        upper_bound = curr_price + (1.2 * volatility * math.sqrt(day / 3.0))

        # Determine directional indicator
        diff_from_base = curr_price - last_price
        pct_change = (diff_from_base / last_price) * 100.0

        if pct_change >= 2.0:
            trend_dir = "rising"
            arrow = "↗️"
            label_ta = "அதிகரிக்கும்"
            label_en = "Rising"
        elif pct_change <= -2.0:
            trend_dir = "falling"
            arrow = "↘️"
            label_ta = "குறையும்"
            label_en = "Falling"
        else:
            trend_dir = "steady"
            arrow = "➡️"
            label_ta = "நிலையானது"
            label_en = "Steady"

        forecast_results.append({
            "day_number": day,
            "date": target_date.strftime("%Y-%m-%d"),
            "day_name_en": target_date.strftime("%a"),
            "day_name_ta": ["திங்கள்", "செவ்வாய்", "புதன்", "வியாழன்", "வெள்ளி", "சனி", "ஞாயிறு"][day_of_week],
            "projected_price": round(curr_price, 0),
            "lower_bound": round(max(lower_bound, 0), 0),
            "upper_bound": round(upper_bound, 0),
            "direction": trend_dir,
            "arrow": arrow,
            "pct_change": round(pct_change, 1),
            "label_ta": label_ta,
            "label_en": label_en,
        })

    return forecast_results
