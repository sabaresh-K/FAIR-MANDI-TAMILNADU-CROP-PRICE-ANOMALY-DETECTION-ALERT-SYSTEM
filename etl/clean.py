"""
ETL Cleaning Module: clean.py
Cleans raw Agmarknet records, standardizes commodity/variety names, parses dates,
converts units to INR/quintal, attaches lat/lng coordinates, deduplicates records,
and writes to SQLite database (mandi_prices.db).
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

from config.config import (
    DB_PATH,
    MANDI_COORDS_PATH,
    DEFAULT_TN_LAT,
    DEFAULT_TN_LNG,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def load_mandi_coords() -> Dict[str, Dict[str, Any]]:
    """Loads static mandi coordinate lookup dictionary."""
    if MANDI_COORDS_PATH.exists():
        try:
            with open(MANDI_COORDS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading mandi coords file: {e}")
    return {}


def parse_date(date_str: Any) -> Optional[str]:
    """Parses various date formats from Agmarknet API into YYYY-MM-DD."""
    if not date_str or pd.isna(date_str):
        return None
    
    date_str = str(date_str).strip()
    
    formats = [
        "%d/%m/%Y",      # e.g., 19/09/2026
        "%Y-%m-%d",      # e.g., 2026-09-19
        "%d-%m-%Y",      # e.g., 19-09-2026
        "%Y/%m/%d",
        "%d %b %Y",      # e.g., 19 Sep 2026
        "%d-%b-%Y",      # e.g., 19-Sep-2026
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    return None


def clean_records(raw_records: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Cleans raw Agmarknet dicts into a structured, standardized Pandas DataFrame.
    """
    if not raw_records:
        return pd.DataFrame()

    df = pd.DataFrame(raw_records)

    # Standardize column name mapping from API response keys
    col_mapping = {
        "arrival_date": "date",
        "Date": "date",
        "state": "state",
        "State": "state",
        "district": "district",
        "District": "district",
        "market": "market",
        "Market": "market",
        "commodity": "commodity",
        "Commodity": "commodity",
        "variety": "variety",
        "Variety": "variety",
        "min_price": "min_price",
        "Min_Price": "min_price",
        "Min Price": "min_price",
        "max_price": "max_price",
        "Max_Price": "max_price",
        "Max Price": "max_price",
        "modal_price": "modal_price",
        "Modal_Price": "modal_price",
        "Modal Price": "modal_price",
        "arrivals_tonnes": "arrivals_tonnes",
        "Arrivals": "arrivals_tonnes",
    }
    df = df.rename(columns=col_mapping)

    required_cols = ["date", "market", "commodity", "modal_price"]
    for col in required_cols:
        if col not in df.columns:
            logger.error(f"Missing required column '{col}' in raw data.")
            return pd.DataFrame()

    # Clean & standardize text fields
    df["state"] = df.get("state", "Tamil Nadu").fillna("Tamil Nadu").astype(str).str.strip()
    df["district"] = df.get("district", "Unknown").fillna("Unknown").astype(str).str.strip().str.title()
    df["market"] = df["market"].fillna("Unknown").astype(str).str.strip().str.title()
    df["commodity"] = df["commodity"].fillna("Unknown").astype(str).str.strip().str.title()
    df["variety"] = df.get("variety", "Common").fillna("Common").astype(str).str.strip().str.title()

    # Parse date
    df["date"] = df["date"].apply(parse_date)
    df = df.dropna(subset=["date"])

    # Clean numeric fields
    for price_col in ["min_price", "max_price", "modal_price"]:
        if price_col in df.columns:
            df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
        else:
            df[price_col] = None

    if "arrivals_tonnes" in df.columns:
        df["arrivals_tonnes"] = pd.to_numeric(df["arrivals_tonnes"], errors="coerce").fillna(0.0)
    else:
        df["arrivals_tonnes"] = 0.0

    # Drop rows with null, non-positive modal prices
    df = df.dropna(subset=["modal_price"])
    df = df[df["modal_price"] > 0]

    # Fill min_price and max_price defaults if missing
    df["min_price"] = df["min_price"].fillna(df["modal_price"])
    df["max_price"] = df["max_price"].fillna(df["modal_price"])

    # Coordinate Lookup Mapping
    coords_map = load_mandi_coords()

    def get_lat_lng(row):
        m_name = row["market"]
        d_name = row["district"]

        # Check exact market match
        for k, v in coords_map.items():
            if k.lower() in m_name.lower() or m_name.lower() in k.lower():
                return pd.Series([v["lat"], v["lng"]])
        
        # Check district match
        for k, v in coords_map.items():
            if v.get("district", "").lower() == d_name.lower():
                return pd.Series([v["lat"], v["lng"]])

        # Default fallback
        return pd.Series([DEFAULT_TN_LAT, DEFAULT_TN_LNG])

    df[["market_lat", "market_lng"]] = df.apply(get_lat_lng, axis=1)

    # Filter state to Tamil Nadu
    df = df[df["state"].str.lower() == "tamil nadu"]

    # Select and order final schema
    final_cols = [
        "date",
        "state",
        "district",
        "market",
        "commodity",
        "variety",
        "min_price",
        "max_price",
        "modal_price",
        "arrivals_tonnes",
        "market_lat",
        "market_lng",
    ]
    df = df[final_cols]

    # Deduplicate by (date, market, commodity, variety)
    df = df.drop_duplicates(subset=["date", "market", "commodity", "variety"], keep="last")

    return df


def save_to_db(df: pd.DataFrame, db_path=DB_PATH) -> int:
    """
    Saves cleaned dataframe to SQLite database, performing UPSERT / ignore duplicate key inserts.
    """
    if df.empty:
        logger.info("DataFrame is empty. Nothing to save.")
        return 0

    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    cursor = conn.cursor()

    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mandi_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            state TEXT NOT NULL,
            district TEXT NOT NULL,
            market TEXT NOT NULL,
            commodity TEXT NOT NULL,
            variety TEXT NOT NULL,
            min_price REAL NOT NULL,
            max_price REAL NOT NULL,
            modal_price REAL NOT NULL,
            arrivals_tonnes REAL DEFAULT 0.0,
            market_lat REAL NOT NULL,
            market_lng REAL NOT NULL,
            UNIQUE(date, market, commodity, variety)
        )
    """)

    # Create indices for fast lookup
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mandi_lookup ON mandi_prices (commodity, market, date);")

    # Upsert logic
    insert_sql = """
        INSERT INTO mandi_prices (
            date, state, district, market, commodity, variety,
            min_price, max_price, modal_price, arrivals_tonnes, market_lat, market_lng
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, market, commodity, variety) DO UPDATE SET
            min_price = excluded.min_price,
            max_price = excluded.max_price,
            modal_price = excluded.modal_price,
            arrivals_tonnes = excluded.arrivals_tonnes,
            market_lat = excluded.market_lat,
            market_lng = excluded.market_lng;
    """

    records = df.to_dict(orient="records")
    inserted_count = 0

    for r in records:
        cursor.execute(
            insert_sql,
            (
                r["date"],
                r["state"],
                r["district"],
                r["market"],
                r["commodity"],
                r["variety"],
                r["min_price"],
                r["max_price"],
                r["modal_price"],
                r["arrivals_tonnes"],
                r["market_lat"],
                r["market_lng"],
            ),
        )
        inserted_count += 1

    conn.commit()
    conn.close()

    logger.info(f"Successfully upserted {inserted_count} records into SQLite DB at {db_path}")
    return inserted_count
