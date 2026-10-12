"""
Cross-Mandi Comparator Module: cross_market.py
Spatial sanity checker using Haversine distance.
Compares price movement of flagged anomalies against nearby mandis on the same date.
Escalates to 'is_confirmed' only if price divergence exceeds threshold (e.g., >15%).
"""

import math
import logging
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

from config.config import (
    CROSS_MARKET_NEIGHBOR_COUNT,
    MAX_NEIGHBOR_DISTANCE_KM,
    CROSS_MARKET_DIVERGENCE_THRESHOLD,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculates the great-circle distance between two points on the Earth surface in kilometers.
    """
    R = 6371.0  # Earth's radius in kilometers

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c


def evaluate_cross_market_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Evaluates spatial cross-mandi consensus for each record.
    Returns DataFrame with added columns:
    - 'nearby_markets': List of names of nearest mandis
    - 'nearby_median_change': Median day-over-day % price change of nearby mandis
    - 'nearby_median_price': Median modal price of nearby mandis
    - 'divergence_pct': Divergence % vs nearby median
    - 'is_confirmed': Boolean flag indicating if anomaly is confirmed by spatial divergence
    """
    df_out = df.copy()

    if df_out.empty:
        df_out["nearby_markets"] = ""
        df_out["nearby_median_change"] = 0.0
        df_out["nearby_median_price"] = 0.0
        df_out["divergence_pct"] = 0.0
        df_out["is_confirmed"] = False
        return df_out

    # Pre-cache pairwise distances between unique markets
    market_coords = (
        df_out[["market", "market_lat", "market_lng"]]
        .dropna()
        .drop_duplicates(subset=["market"])
        .set_index("market")
    )
    markets_list = list(market_coords.index)
    dist_cache = {}
    for i in range(len(markets_list)):
        m1 = markets_list[i]
        lat1 = market_coords.loc[m1, "market_lat"]
        lng1 = market_coords.loc[m1, "market_lng"]
        for j in range(i + 1, len(markets_list)):
            m2 = markets_list[j]
            lat2 = market_coords.loc[m2, "market_lat"]
            lng2 = market_coords.loc[m2, "market_lng"]
            d = haversine_distance(lat1, lng1, lat2, lng2)
            dist_cache[(m1, m2)] = d
            dist_cache[(m2, m1)] = d

    # Results dictionary keyed by DataFrame row index
    res_nearby_markets = {}
    res_nearby_median_change = {}
    res_nearby_median_price = {}
    res_divergence_pct = {}
    res_is_confirmed = {}

    grouped = df_out.groupby(["date", "commodity"])

    for (date_val, comm_val), group in grouped:
        if len(group) < 2:
            for idx in group.index:
                if group.loc[idx, "is_anomaly"]:
                    res_is_confirmed[idx] = bool(group.loc[idx, "anomaly_score"] >= 0.75)
                else:
                    res_is_confirmed[idx] = False
            continue

        records = group.to_dict(orient="records")
        idx_list = group.index.tolist()

        for i, target in enumerate(records):
            target_idx = idx_list[i]
            target_market = target["market"]
            t_pct_change = target.get("pct_change_dod", 0.0) or 0.0
            t_price = target["modal_price"]
            is_anom = bool(target.get("is_anomaly", False))
            anom_score = float(target.get("anomaly_score", 0.0))

            # Find neighbors using cached distance
            neighbors = []
            for j, neighbor in enumerate(records):
                if i == j:
                    continue
                n_market = neighbor["market"]
                if n_market == target_market:
                    continue

                dist = dist_cache.get((target_market, n_market))
                if dist is None:
                    dist = haversine_distance(
                        target["market_lat"], target["market_lng"],
                        neighbor["market_lat"], neighbor["market_lng"]
                    )
                    dist_cache[(target_market, n_market)] = dist

                if dist <= MAX_NEIGHBOR_DISTANCE_KM:
                    neighbors.append((
                        dist,
                        n_market,
                        neighbor["modal_price"],
                        neighbor.get("pct_change_dod", 0.0) or 0.0
                    ))

            if not neighbors:
                if is_anom:
                    res_is_confirmed[target_idx] = bool(anom_score >= 0.75)
                else:
                    res_is_confirmed[target_idx] = False
                continue

            neighbors.sort(key=lambda x: x[0])
            nearest = neighbors[:CROSS_MARKET_NEIGHBOR_COUNT]

            neighbor_names = [n[1] for n in nearest]
            neighbor_prices = [n[2] for n in nearest]
            neighbor_changes = [n[3] for n in nearest]

            med_change = float(np.median(neighbor_changes))
            med_price = float(np.median(neighbor_prices))

            change_divergence = abs(t_pct_change - med_change)
            price_divergence = abs(t_price - med_price) / (med_price + 1e-6) * 100.0
            divergence_pct = max(change_divergence, price_divergence)

            res_nearby_markets[target_idx] = ", ".join(neighbor_names)
            res_nearby_median_change[target_idx] = round(med_change, 2)
            res_nearby_median_price[target_idx] = round(med_price, 2)
            res_divergence_pct[target_idx] = round(divergence_pct, 2)

            if is_anom:
                if divergence_pct >= CROSS_MARKET_DIVERGENCE_THRESHOLD:
                    res_is_confirmed[target_idx] = True
                else:
                    res_is_confirmed[target_idx] = False
            else:
                res_is_confirmed[target_idx] = False

    df_out["nearby_markets"] = [res_nearby_markets.get(i, "") for i in df_out.index]
    df_out["nearby_median_change"] = [res_nearby_median_change.get(i, 0.0) for i in df_out.index]
    df_out["nearby_median_price"] = [res_nearby_median_price.get(i, 0.0) for i in df_out.index]
    df_out["divergence_pct"] = [res_divergence_pct.get(i, 0.0) for i in df_out.index]
    df_out["is_confirmed"] = [res_is_confirmed.get(i, False) for i in df_out.index]

    return df_out
