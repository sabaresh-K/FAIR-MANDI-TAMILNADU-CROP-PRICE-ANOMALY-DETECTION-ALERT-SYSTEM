"""
Decision Engine Module: decision.py
Farmer-Centric Decision Classifier.
Combines anomaly signals, short-term price forecast, and cross-mandi transport costs
to produce simple Traffic-Light (Sell Now 🟢 / Hold 🔴 / Normal 🟡) advice in Tamil & English.
"""

import math
from typing import Dict, Any, List
import pandas as pd
import numpy as np

from models.cross_market import haversine_distance

# Freight tariff benchmark: approx Rs 2.50 per quintal per 10 km (for small commercial vehicle load)
TRANSPORT_COST_PER_KM_PER_QUINTAL = 0.25  # ~₹25 per 100km per quintal


def compute_farmer_decision(
    latest_record: Dict[str, Any],
    forecast_7days: List[Dict[str, Any]],
    nearby_records: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Computes actionable farmer advice with traffic light status and bilingual text.
    """
    commodity = latest_record.get("commodity", "Crop")
    market = latest_record.get("market", "Mandi")
    price = float(latest_record.get("modal_price", 0.0))
    pct_change = float(latest_record.get("pct_change_dod", 0.0))
    is_anomaly = bool(latest_record.get("is_anomaly", False))
    is_confirmed = bool(latest_record.get("is_confirmed", False))

    # Evaluate forecast slope
    forecast_slope = 0.0
    if forecast_7days and len(forecast_7days) >= 3:
        p3 = forecast_7days[2]["projected_price"]
        forecast_slope = ((p3 - price) / price) * 100.0

    # Decision Logic:
    # 🟢 SELL NOW if price spiked abnormally (+15% or confirmed anomaly) OR prices expected to drop sharply
    # 🔴 HOLD / DELAY if prices crashed abnormally (-15%) AND forecast expects recovery
    # 🟡 NORMAL if steady market
    if (is_confirmed and pct_change > 0) or pct_change >= 20.0 or (pct_change >= 10.0 and forecast_slope < -3.0):
        decision_code = "SELL_NOW"
        color = "green"
        traffic_icon = "🟢"
        badge_ta = "இன்றே விற்கவும்!"
        badge_en = "SELL TODAY!"
        sub_ta = f"சந்தை விலை {abs(round(pct_change, 1))}% திடீரென உயர்ந்துள்ளது. விலை குறையும் முன் இன்றே விற்கவும்."
        sub_en = f"Price spiked {abs(round(pct_change, 1))}% today. Take advantage before market cools down."
        speech_ta = f"{market} சந்தையில் {commodity} விலை இன்று ஒரு குவிண்டாலுக்கு {int(price)} ரூபாயாக உயர்ந்துள்ளது. இது நல்ல விலை. இன்றே விற்கவும்."
        speech_en = f"{commodity} price at {market} mandi rose to {int(price)} rupees per quintal today. This is a great price. Sell today."
    elif (is_confirmed and pct_change < 0) or pct_change <= -18.0:
        decision_code = "HOLD"
        color = "red"
        traffic_icon = "🔴"
        badge_ta = "காத்திருக்கவும் / வேறு சந்தை பார்க்கவும்"
        badge_en = "HOLD / CHECK NEARBY MANDI"
        sub_ta = f"விலை {abs(round(pct_change, 1))}% சரிந்துள்ளது. 2-3 நாட்கள் பொறுக்கவும் அல்லது அருகிலுள்ள சந்தையில் விற்கவும்."
        sub_en = f"Price fell {abs(round(pct_change, 1))}%. Consider holding 2-3 days or selling in a neighboring mandi."
        speech_ta = f"{market} சந்தையில் {commodity} விலை இன்று மிகவும் குறைந்துள்ளது. உடனடியாக விற்க வேண்டாம். இரண்டு நாட்கள் காத்திருக்கவும் அல்லது பக்கத்து ஊர் சந்தைக்கு கொண்டு செல்லவும்."
        speech_en = f"{commodity} price at {market} mandi dropped sharply today. Consider holding for 2 to 3 days or selling in a nearby mandi."
    else:
        decision_code = "NORMAL"
        color = "yellow"
        traffic_icon = "🟡"
        badge_ta = "வழக்கமான சந்தை விலை"
        badge_en = "NORMAL MARKET PRICE"
        sub_ta = "விலையில் பெரிய மாற்றம் இல்லை. உங்கள் தேவைக்கேற்ப விற்கலாம்."
        sub_en = "Prices are stable within seasonal expectations. Safe to sell as planned."
        speech_ta = f"{market} சந்தையில் {commodity} விலை ஒரு குவிண்டாலுக்கு {int(price)} ரூபாய் என நிலையாக உள்ளது."
        speech_en = f"{commodity} price at {market} mandi is steady at {int(price)} rupees per quintal."

    # Compute Nearby Mandi Arbitrage options
    nearby_options = []
    target_district = latest_record.get("district", "").strip()

    if nearby_records:
        t_lat = latest_record.get("market_lat", 0.0)
        t_lng = latest_record.get("market_lng", 0.0)

        for n in nearby_records:
            if n.get("market", "").lower() == market.lower():
                continue

            n_lat = n.get("market_lat", 0.0)
            n_lng = n.get("market_lng", 0.0)
            n_price = float(n.get("modal_price", 0.0))
            n_market = n.get("market", "")
            n_district = n.get("district", "").strip()

            dist_km = haversine_distance(t_lat, t_lng, n_lat, n_lng)
            is_same_district = (n_district.lower() == target_district.lower()) if (n_district and target_district) else False

            if dist_km <= 200.0 or is_same_district:
                est_transport = dist_km * TRANSPORT_COST_PER_KM_PER_QUINTAL
                price_diff = n_price - price
                net_gain = price_diff - est_transport

                nearby_options.append({
                    "market": n_market,
                    "district": n_district,
                    "is_same_district": is_same_district,
                    "lat": n_lat,
                    "lng": n_lng,
                    "distance_km": round(dist_km, 0),
                    "modal_price": round(n_price, 0),
                    "price_diff": round(price_diff, 0),
                    "transport_cost": round(est_transport, 0),
                    "net_gain": round(net_gain, 0),
                    "is_profitable": net_gain > 50.0,
                })

        # Sort by highest net gain
        nearby_options.sort(key=lambda x: x["net_gain"], reverse=True)

    # 1. District Highest Price Mandi (Within the same district as the searched mandi)
    district_mandis = [n for n in nearby_options if n.get("is_same_district")]
    district_higher = [n for n in district_mandis if n["price_diff"] > 0]
    district_higher.sort(key=lambda x: (x["modal_price"], -x["distance_km"]), reverse=True)
    district_highest_mandi = district_higher[0] if district_higher else None
    is_district_champion = (district_highest_mandi is None)

    # 2. Regional Highest Price Mandi (within ~200 km)
    regional_higher = [n for n in nearby_options if n["price_diff"] > 0]
    regional_higher.sort(key=lambda x: (x["modal_price"], -x["distance_km"]), reverse=True)
    regional_highest_mandi = regional_higher[0] if regional_higher else None

    # STRICT DISTRICT SCOPE:
    # Farmer searched a mandi in district X; compare ONLY within district X!
    # Do NOT reach out to distant outside districts like Madurai.
    if target_district:
        best_higher_price_mandi = district_highest_mandi
    else:
        best_higher_price_mandi = district_highest_mandi if district_highest_mandi else regional_highest_mandi

    return {
        "decision": decision_code,
        "color": color,
        "traffic_icon": traffic_icon,
        "badge_ta": badge_ta,
        "badge_en": badge_en,
        "explanation_ta": sub_ta,
        "explanation_en": sub_en,
        "speech_ta": speech_ta,
        "speech_en": speech_en,
        "modal_price": round(price, 0),
        "pct_change_dod": round(pct_change, 1),
        "district": target_district,
        "district_highest_mandi": district_highest_mandi,
        "is_district_champion": is_district_champion,
        "nearby_options": nearby_options[:8],
        "higher_price_mandis": regional_higher[:8],
        "best_higher_price_mandi": best_higher_price_mandi,
        "has_higher_price_nearby": bool(best_higher_price_mandi),
    }
