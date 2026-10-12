"""
Synthetic Data Generator: generate_synthetic_data.py
Populates data/processed/mandi_prices.db with realistic 90-day time series data
across major Tamil Nadu crops and mandis, including realistic injected anomalies.
"""

import math
import random
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from etl.clean import save_to_db, load_mandi_coords

# Load all TN Mandis & Coordinates dynamically from config/mandi_coords.json
_coords_dict = load_mandi_coords()
if _coords_dict:
    MANDIS = [
        {
            "market": name,
            "district": info.get("district", name),
            "lat": info["lat"],
            "lng": info["lng"]
        }
        for name, info in _coords_dict.items()
    ]
else:
    MANDIS = [
        {"market": "Madurai", "district": "Madurai", "lat": 9.9252, "lng": 78.1198},
        {"market": "Koyambedu", "district": "Chennai", "lat": 13.0694, "lng": 80.1948},
        {"market": "Coimbatore", "district": "Coimbatore", "lat": 11.0168, "lng": 76.9558},
        {"market": "Tiruchirappalli", "district": "Tiruchirappalli", "lat": 10.7905, "lng": 78.7047},
        {"market": "Salem", "district": "Salem", "lat": 11.6643, "lng": 78.1460},
        {"market": "Erode", "district": "Erode", "lat": 11.3410, "lng": 77.7172},
        {"market": "Dindigul", "district": "Dindigul", "lat": 10.3673, "lng": 77.9803},
        {"market": "Tirunelveli", "district": "Tirunelveli", "lat": 8.7139, "lng": 77.7567},
        {"market": "Vellore", "district": "Vellore", "lat": 12.9165, "lng": 79.1325},
        {"market": "Thanjavur", "district": "Thanjavur", "lat": 10.7870, "lng": 79.1378},
    ]

# Base Price Config (INR/Quintal)
CROPS = {
    "Tomato": {"base_price": 2800, "volatility": 0.08, "variety": "Local"},
    "Onion": {"base_price": 3200, "volatility": 0.05, "variety": "Big"},
    "Potato": {"base_price": 2200, "volatility": 0.03, "variety": "Jyoti"},
    "Rice": {"base_price": 4500, "volatility": 0.02, "variety": "Ponni"},
    "Banana": {"base_price": 1800, "volatility": 0.04, "variety": "Poovan"},
}


def generate_synthetic_dataset(num_days: int = 90) -> pd.DataFrame:
    """Generates synthetic time series dataset for TN mandis."""
    np.random.seed(42)
    random.seed(42)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=num_days)
    date_list = [start_date + timedelta(days=i) for i in range(num_days)]

    records = []

    for crop_name, config in CROPS.items():
        base_p = config["base_price"]
        vol = config["volatility"]
        variety = config["variety"]

        for mandi in MANDIS:
            market_name = mandi["market"]
            m_lat = mandi["lat"]
            m_lng = mandi["lng"]
            district = mandi["district"]

            # Generate baseline AR(1) price series with weekly seasonality
            price = base_p + random.uniform(-100, 100)
            trend_factor = random.uniform(-2, 2)

            for day_idx, dt in enumerate(date_list):
                date_str = dt.strftime("%Y-%m-%d")
                day_of_week = dt.weekday()
                seasonal = math.sin(day_of_week / 7.0 * 2 * math.pi) * (base_p * 0.03)

                # AR(1) process
                noise = np.random.normal(0, base_p * vol)
                price = 0.85 * price + 0.15 * (base_p + day_idx * trend_factor) + noise + seasonal
                price = max(price, base_p * 0.4)  # Floor price

                arrivals = max(5.0, np.random.normal(25.0, 5.0))

                records.append({
                    "date": date_str,
                    "state": "Tamil Nadu",
                    "district": district,
                    "market": market_name,
                    "commodity": crop_name,
                    "variety": variety,
                    "min_price": round(price * 0.95, 2),
                    "max_price": round(price * 1.05, 2),
                    "modal_price": round(price, 2),
                    "arrivals_tonnes": round(arrivals, 2),
                    "market_lat": m_lat,
                    "market_lng": m_lng,
                })

    df = pd.DataFrame(records)

    # Inject specific isolated anomalies on recent dates for demonstration
    latest_date_str = date_list[-1].strftime("%Y-%m-%d")
    prev_date_str = date_list[-3].strftime("%Y-%m-%d")

    # Real pricing without artificial statewide distortions

    # Anomaly 2: Severe Onion price crash in Koyambedu on latest date (-45%)
    mask2 = (df["market"] == "Koyambedu") & (df["commodity"] == "Onion") & (df["date"] == latest_date_str)
    df.loc[mask2, "modal_price"] = df.loc[mask2, "modal_price"] * 0.55
    df.loc[mask2, "min_price"] = df.loc[mask2, "min_price"] * 0.50

    # Anomaly 3: Potato price spike in Salem 3 days ago (+40%)
    mask3 = (df["market"] == "Salem") & (df["commodity"] == "Potato") & (df["date"] == prev_date_str)
    df.loc[mask3, "modal_price"] = df.loc[mask3, "modal_price"] * 1.40

from etl.clean import save_to_db, load_mandi_coords
from etl.fetch_live_real_prices import generate_real_mandi_dataset, populate_real_market_data

def generate_synthetic_dataset(num_days: int = 90) -> pd.DataFrame:
    """Generates authentic Tamil Nadu mandi dataset grounded in real market prices."""
    return generate_real_mandi_dataset(num_days)


if __name__ == "__main__":
    print("Generating authentic Tamil Nadu mandi price data grounded in real wholesale values...")
    count = populate_real_market_data(90)
    print(f"Done! Saved {count} authentic price records to database.")

