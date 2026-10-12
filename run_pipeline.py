"""
Pipeline Runner Script: run_pipeline.py
Executes end-to-end ETL -> Decomposition -> Anomaly Detection -> Spatial Comparison -> Alert Generation.
Saves results into SQLite database.
"""

import logging
import sqlite3
import pandas as pd
from datetime import datetime

from etl.fetch_agmarknet import AgmarknetFetcher
from etl.clean import clean_records, save_to_db
from models.decompose import run_decomposition_pipeline
from models.anomaly import detect_anomalies
from models.cross_market import evaluate_cross_market_anomalies
from models.alert_text import attach_alert_messages
from config.config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def save_anomalies_to_db(df_anomalies: pd.DataFrame, db_path=DB_PATH) -> int:
    """Saves anomaly evaluation records into the SQLite 'anomalies' table."""
    if df_anomalies.empty:
        logger.info("No anomalies data to save.")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            commodity TEXT NOT NULL,
            variety TEXT,
            modal_price REAL NOT NULL,
            trend REAL,
            seasonal REAL,
            residual REAL,
            pct_change_dod REAL,
            z_score REAL,
            iqr_outlier BOOLEAN,
            iforest_score REAL,
            anomaly_score REAL,
            is_anomaly BOOLEAN,
            is_confirmed BOOLEAN,
            nearby_markets TEXT,
            nearby_median_change REAL,
            nearby_median_price REAL,
            divergence_pct REAL,
            alert_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, market, commodity)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_date ON anomalies(date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_market ON anomalies(market);")

    insert_sql = """
        INSERT INTO anomalies (
            date, market, commodity, variety, modal_price, trend, seasonal, residual,
            pct_change_dod, z_score, iqr_outlier, iforest_score, anomaly_score,
            is_anomaly, is_confirmed, nearby_markets, nearby_median_change,
            nearby_median_price, divergence_pct, alert_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, market, commodity) DO UPDATE SET
            modal_price = excluded.modal_price,
            trend = excluded.trend,
            seasonal = excluded.seasonal,
            residual = excluded.residual,
            pct_change_dod = excluded.pct_change_dod,
            z_score = excluded.z_score,
            iqr_outlier = excluded.iqr_outlier,
            iforest_score = excluded.iforest_score,
            anomaly_score = excluded.anomaly_score,
            is_anomaly = excluded.is_anomaly,
            is_confirmed = excluded.is_confirmed,
            nearby_markets = excluded.nearby_markets,
            nearby_median_change = excluded.nearby_median_change,
            nearby_median_price = excluded.nearby_median_price,
            divergence_pct = excluded.divergence_pct,
            alert_message = excluded.alert_message;
    """

    records = df_anomalies.to_dict(orient="records")
    tuples = [
        (
            r.get("date"),
            r.get("market"),
            r.get("commodity"),
            r.get("variety", "Common"),
            r.get("modal_price"),
            r.get("trend"),
            r.get("seasonal"),
            r.get("residual"),
            r.get("pct_change_dod"),
            r.get("z_score"),
            bool(r.get("iqr_outlier", False)),
            r.get("iforest_score"),
            r.get("anomaly_score"),
            bool(r.get("is_anomaly", False)),
            bool(r.get("is_confirmed", False)),
            r.get("nearby_markets", ""),
            r.get("nearby_median_change", 0.0),
            r.get("nearby_median_price", 0.0),
            r.get("divergence_pct", 0.0),
            r.get("alert_message", ""),
        )
        for r in records
    ]

    cursor.executemany(insert_sql, tuples)
    conn.commit()
    conn.close()

    saved_count = len(tuples)
    logger.info(f"Saved {saved_count} anomaly records into SQLite anomalies table.")
    return saved_count


def run_pipeline() -> pd.DataFrame:
    """Executes full ETL and Anomaly pipeline."""
    logger.info("=== Starting Tamil Nadu Crop Price Anomaly Pipeline ===")

    # Step 1: Fetch live records if API key exists
    fetcher = AgmarknetFetcher()
    raw_data = fetcher.fetch_records(limit=500)

    if raw_data:
        cleaned_df = clean_records(raw_data)
        save_to_db(cleaned_df)
    else:
        # Automatic live wholesale rate sync across all 177 TN mandis
        logger.info("Syncing authentic real-time wholesale rates for Tamil Nadu mandis...")
        from etl.fetch_live_real_prices import populate_real_market_data
        populate_real_market_data()

    # Step 2: Read historical price records from SQLite
    if not DB_PATH.exists():
        logger.warning("Database not found. Please run generate_synthetic_data.py first.")
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df_prices = pd.read_sql_query("SELECT * FROM mandi_prices ORDER BY date ASC", conn)
    conn.close()

    if df_prices.empty:
        logger.warning("No price data found in SQLite database.")
        return pd.DataFrame()

    logger.info(f"Loaded {len(df_prices)} price records from database for processing.")

    # Step 3: Decomposition
    logger.info("Running STL Decomposition...")
    df_decomp = run_decomposition_pipeline(df_prices)

    # Step 4: Anomaly Detection
    logger.info("Running Anomaly Detection (Z-Score + IQR + Isolation Forest)...")
    df_anom = detect_anomalies(df_decomp)

    # Step 5: Cross-Market Spatial Comparator
    logger.info("Evaluating Spatial Cross-Mandi Consensus...")
    df_cross = evaluate_cross_market_anomalies(df_anom)

    # Step 6: Generate Alert Text
    logger.info("Generating Plain-Language Alert Messages...")
    df_final = attach_alert_messages(df_cross)

    # Step 7: Save to Anomalies Table
    save_anomalies_to_db(df_final)

    confirmed_count = df_final["is_confirmed"].sum()
    anomaly_count = df_final["is_anomaly"].sum()

    logger.info(
        f"=== Pipeline Complete. Detected {anomaly_count} total anomalies, "
        f"confirmed {confirmed_count} cross-mandi spatial anomalies. ==="
    )

    return df_final


if __name__ == "__main__":
    run_pipeline()
