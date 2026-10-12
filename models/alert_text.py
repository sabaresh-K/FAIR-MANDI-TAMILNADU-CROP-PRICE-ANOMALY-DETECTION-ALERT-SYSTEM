"""
Alert Generator Module: alert_text.py
Generates plain-language, farmer-friendly alert text messages for confirmed anomalies.
"""

from typing import Dict, Any
import pandas as pd


def generate_alert_message(record: Dict[str, Any]) -> str:
    """
    Generates a template-based plain language alert string for a crop price anomaly record.
    
    Expected keys in record dict:
    - commodity (str)
    - market (str)
    - modal_price (float)
    - pct_change_dod (float)
    - nearby_markets (str)
    - nearby_median_change (float)
    - nearby_median_price (float)
    """
    commodity = str(record.get("commodity", "Crop")).title()
    market = str(record.get("market", "Market")).title()
    price = float(record.get("modal_price", 0.0))
    pct_change = float(record.get("pct_change_dod", 0.0))
    
    neighbor_str = record.get("nearby_markets", "")
    neighbor_pct = float(record.get("nearby_median_change", 0.0))

    direction = "rose" if pct_change >= 0 else "fell"
    abs_pct = abs(round(pct_change, 1))
    abs_neighbor_pct = abs(round(neighbor_pct, 1))
    neighbor_direction = "rose" if neighbor_pct >= 0 else "fell"

    formatted_price = f"₹{price:,.0f}/quintal"

    if neighbor_str:
        neighbor_part = f"Nearby markets ({neighbor_str}) show only a {abs_neighbor_pct}% {neighbor_direction}"
    else:
        neighbor_part = "Neighboring regional markets show significantly different pricing"

    if direction == "rose":
        recommendation = "Consider selling soon to take advantage of peak prices before a potential market correction."
    else:
        recommendation = "Consider delaying sale or checking nearby mandis if transport is feasible."

    alert_msg = (
        f"{commodity} prices at {market} {direction} {abs_pct}% today, reaching {formatted_price}. "
        f"{neighbor_part} — this looks unusual. {recommendation}"
    )

    return alert_msg


def attach_alert_messages(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attaches alert messages to all confirmed anomalies in DataFrame.
    """
    import pandas as pd  # local import safeguard

    df_out = df.copy()

    if df_out.empty:
        df_out["alert_message"] = ""
        return df_out

    records = df_out.to_dict(orient="records")
    msgs = [
        generate_alert_message(r) if (r.get("is_confirmed", False) or r.get("is_anomaly", False)) else ""
        for r in records
    ]
    df_out["alert_message"] = msgs

    return df_out
