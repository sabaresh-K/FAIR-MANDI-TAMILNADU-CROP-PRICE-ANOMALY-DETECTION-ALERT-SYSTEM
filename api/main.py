"""
FastAPI Backend Application: api/main.py
Provides REST API endpoints for price history, anomaly alerts, market lists,
and includes an APScheduler background task to run the ETL/Anomaly pipeline daily.
"""

import sqlite3
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.config import DB_PATH, BASE_DIR
from run_pipeline import run_pipeline
from models.forecast import generate_7day_forecast
from models.decision import compute_farmer_decision
from api.auth import (
    init_auth_tables,
    register_user,
    login_user,
    get_user_by_token,
    logout_user,
    update_user_profile,
    get_database_auth_status,
    RegisterRequest,
    LoginRequest,
    ProfileUpdateRequest
)
from pydantic import BaseModel
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fair Mandi AI - Crop Price Anomaly & Market Intelligence System",
    description="Fair Mandi AI detects abnormal mandi price movements in Tamil Nadu and generates real-time alerts for farmers.",
    version="1.0.0",
)

# Enable CORS for dashboard access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# APScheduler Background Scheduler
scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def startup_event():
    """Initializes background scheduler for daily pipeline run and checks database freshness."""
    logger.info("Initializing SQLite WAL mode...")
    try:
        if DB_PATH.exists():
            c = sqlite3.connect(DB_PATH, timeout=30.0)
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA busy_timeout=30000;")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mandi_prices_comm_mkt ON mandi_prices (LOWER(commodity), LOWER(market), date);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_comm_mkt ON anomalies (LOWER(commodity), LOWER(market), date);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mandi_prices_date_comm ON mandi_prices (date, LOWER(commodity));")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mandi_prices_mkt_date ON mandi_prices (LOWER(market), date);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_mkt_date ON anomalies (LOWER(market), date);")
            c.commit()
            c.close()
    except Exception as e:
        logger.warning(f"Error configuring SQLite WAL mode & indexes: {e}")

    try:
        init_auth_tables()
    except Exception as e:
        logger.warning(f"Error initializing auth tables: {e}")

    logger.info("Starting APScheduler for daily pipeline job...")
    # Schedule pipeline to run daily at 06:00 AM
    scheduler.add_job(run_pipeline, "cron", hour=6, minute=0, id="daily_pipeline")
    scheduler.start()

    # Check database freshness: if max_date < today or missing, sync immediately in background
    try:
        from datetime import datetime
        from etl.fetch_live_real_prices import update_today_live_prices
        today_str = datetime.now().strftime("%Y-%m-%d")
        needs_sync = False
        if not DB_PATH.exists():
            needs_sync = True
        else:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mandi_prices'")
            if not cur.fetchone():
                needs_sync = True
            else:
                cur.execute("SELECT MAX(date) FROM mandi_prices")
                row = cur.fetchone()
                max_date = row[0] if row else None
                if not max_date or max_date < today_str:
                    needs_sync = True
                    logger.info(f"Database data is from {max_date}, today is {today_str}. Initiating live market data sync...")
            conn.close()

        if needs_sync:
            logger.info("Triggering automatic fast sync for today's live market prices...")
            scheduler.add_job(update_today_live_prices, "date", id="startup_sync")
    except Exception as e:
        logger.warning(f"Error checking data freshness on startup: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown background scheduler."""
    logger.info("Shutting down APScheduler...")
    scheduler.shutdown()


def get_db_connection():
    """Helper to open SQLite database connection with WAL mode and robust timeout."""
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail="Database file not found. Please run generate_synthetic_data.py or /pipeline/run first."
        )
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@app.get("/")
def read_root():
    return {
        "system": "Fair Mandi AI - Crop Price Anomaly & Market Intelligence System",
        "status": "online",
        "documentation": "/docs",
        "dashboard": "/dashboard/index.html",
    }


# ---------------- AUTH & FARMER PROFILE ENDPOINTS ----------------

@app.post("/api/auth/register")
def api_register(req: RegisterRequest):
    """Registers a new farmer user and creates an active session."""
    try:
        return register_user(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")


@app.post("/api/auth/login")
def api_login(req: LoginRequest):
    """Authenticates farmer with phone and password/PIN."""
    try:
        return login_user(req)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Error logging in: {e}")
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")


@app.get("/api/auth/me")
def api_me(authorization: Optional[str] = Header(None), token: Optional[str] = Query(None)):
    """Retrieves current logged in farmer's profile from token."""
    auth_token = token
    if not auth_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            auth_token = parts[1]
        else:
            auth_token = authorization

    if not auth_token:
        raise HTTPException(status_code=401, detail="Missing authentication token.")

    user = get_user_by_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please login again.")
    return {"user": user}


@app.post("/api/auth/logout")
def api_logout(authorization: Optional[str] = Header(None), token: Optional[str] = Query(None)):
    """Revokes active farmer session."""
    auth_token = token
    if not auth_token and authorization:
        parts = authorization.split()
        auth_token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization

    if auth_token:
        logout_user(auth_token)
    return {"success": True, "message": "Logged out successfully."}


@app.put("/api/auth/profile")
def api_update_profile(req: ProfileUpdateRequest, authorization: Optional[str] = Header(None), token: Optional[str] = Query(None)):
    """Updates farmer preferences and profile details."""
    auth_token = token
    if not auth_token and authorization:
        parts = authorization.split()
        auth_token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization

    if not auth_token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user = get_user_by_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")

    try:
        updated = update_user_profile(user["id"], req)
        return {"success": True, "user": updated}
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(status_code=500, detail="Profile update failed.")


@app.get("/api/auth/database-info")
def api_database_info():
    """Returns database connection status and real registered farmer statistics from SQLite."""
    return get_database_auth_status()


@app.get("/commodities", response_model=List[str])
def get_commodities():
    """Returns list of all unique commodities in database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT commodity FROM mandi_prices ORDER BY commodity ASC;")
    rows = cursor.fetchall()
    conn.close()
    return [r["commodity"] for r in rows]


@app.get("/markets", response_model=List[str])
def get_markets():
    """Returns list of all unique markets in database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT market FROM mandi_prices ORDER BY market ASC;")
    rows = cursor.fetchall()
    conn.close()
    return [r["market"] for r in rows]


@app.get("/markets-detailed")
def get_markets_detailed():
    """Returns list of all markets with district and coordinates."""
    if "markets_detailed" in _API_CACHE:
        return _API_CACHE["markets_detailed"]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT market, district, market_lat, market_lng
        FROM mandi_prices
        WHERE market IS NOT NULL AND market != ''
        ORDER BY district ASC, market ASC;
    """)
    rows = cursor.fetchall()
    conn.close()
    res = [dict(r) for r in rows]
    _API_CACHE["markets_detailed"] = res
    return res


SUPPORTED_CROPS = [
    {"id": "Tomato", "name_en": "Tomato", "name_ta": "தக்காளி", "variety": "Local"},
    {"id": "Onion", "name_en": "Onion", "name_ta": "வெங்காயம்", "variety": "Big"},
    {"id": "Rice", "name_en": "Paddy / Rice", "name_ta": "நெல் / அரிசி", "variety": "Ponni"},
    {"id": "Potato", "name_en": "Potato", "name_ta": "உருளைக்கிழங்கு", "variety": "Jyoti"},
    {"id": "Banana", "name_en": "Banana", "name_ta": "வாழைப்பழம்", "variety": "Poovan"},
    {"id": "Green Chilli", "name_en": "Green Chilli", "name_ta": "பச்சை மிளகாய்", "variety": "G4"},
    {"id": "Brinjal", "name_en": "Brinjal", "name_ta": "கத்திரிக்காய்", "variety": "Round"},
    {"id": "Carrot", "name_en": "Carrot", "name_ta": "கேரட்", "variety": "Ooty"},
    {"id": "Coconut", "name_en": "Coconut", "name_ta": "தேங்காய்", "variety": "Pollachi"},
    {"id": "Ladies Finger", "name_en": "Ladies Finger", "name_ta": "வெண்டைக்காய்", "variety": "Hybrid"},
    {"id": "Garlic", "name_en": "Garlic", "name_ta": "பூண்டு", "variety": "Hill Garlic"},
    {"id": "Ginger", "name_en": "Ginger", "name_ta": "இஞ்சி", "variety": "Green Ginger"},
    {"id": "Cabbage", "name_en": "Cabbage", "name_ta": "முட்டைக்கோஸ்", "variety": "Green"},
    {"id": "Cauliflower", "name_en": "Cauliflower", "name_ta": "காலிஃபிளவர்", "variety": "Snowball"},
    {"id": "Drumstick", "name_en": "Drumstick", "name_ta": "முருங்கைக்காய்", "variety": "PKM-1"},
    {"id": "Beetroot", "name_en": "Beetroot", "name_ta": "பீட்ரூட்", "variety": "Local"},
    {"id": "Coriander", "name_en": "Coriander", "name_ta": "கொத்தமல்லி", "variety": "Leaves"},
    {"id": "Mango", "name_en": "Mango", "name_ta": "மாம்பழம்", "variety": "Salem / Alphonso"},
    {"id": "Turmeric", "name_en": "Turmeric", "name_ta": "மஞ்சள்", "variety": "Erode Finger"},
    {"id": "Cotton", "name_en": "Cotton", "name_ta": "பருத்தி", "variety": "MCU-5"},
]


# High-speed in-memory LRU/response cache for sub-millisecond repeat queries
_API_CACHE: Dict[str, Any] = {}

def clear_api_cache():
    _API_CACHE.clear()


@app.get("/crops")
def get_supported_crops():
    """Returns supported crops with bilingual names and visual icons."""
    return SUPPORTED_CROPS


@app.get("/decision/{commodity}/{market}")
def get_farmer_decision(commodity: str, market: str):
    """
    Returns high-level Traffic Light Decision (Sell Now 🟢 / Hold 🔴 / Normal 🟡),
    bilingual advice, 7-day visual forecast, and nearby mandi arbitrage options.
    """
    cache_key = f"decision_{commodity.strip().lower()}_{market.strip().lower()}"
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]

    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch historical prices for this commodity and market
    query_history = """
        SELECT p.date, p.market, p.district, p.commodity, p.modal_price, p.market_lat, p.market_lng,
               COALESCE(a.pct_change_dod, 0) as pct_change_dod,
               COALESCE(a.anomaly_score, 0) as anomaly_score,
               COALESCE(a.is_anomaly, 0) as is_anomaly,
               COALESCE(a.is_confirmed, 0) as is_confirmed
        FROM mandi_prices p
        LEFT JOIN anomalies a
            ON p.date = a.date AND LOWER(p.market) = LOWER(a.market) AND LOWER(p.commodity) = LOWER(a.commodity)
        WHERE LOWER(p.commodity) = LOWER(?) AND LOWER(p.market) = LOWER(?)
        ORDER BY p.date ASC;
    """
    cursor.execute(query_history, (commodity, market))
    history_rows = [dict(r) for r in cursor.fetchall()]

    if not history_rows:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail=f"No data found for commodity '{commodity}' in market '{market}'."
        )

    latest_record = history_rows[-1]
    latest_date = latest_record["date"]

    # Fetch other mandis reporting the same commodity on the latest date
    query_nearby = """
        SELECT market, district, commodity, modal_price, market_lat, market_lng
        FROM mandi_prices
        WHERE date = ? AND LOWER(commodity) = LOWER(?) AND LOWER(market) != LOWER(?);
    """
    cursor.execute(query_nearby, (latest_date, commodity, market))
    nearby_records = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Generate 7-day forecast
    df_history = pd.DataFrame(history_rows)
    forecast_7days = generate_7day_forecast(df_history, days_ahead=7)

    # Compute farmer decision and nearby mandi arbitrage
    decision_result = compute_farmer_decision(
        latest_record=latest_record,
        forecast_7days=forecast_7days,
        nearby_records=nearby_records,
    )

    decision_result["forecast_7days"] = forecast_7days
    decision_result["commodity"] = commodity
    decision_result["market"] = market
    decision_result["district"] = latest_record.get("district", "Tamil Nadu")
    decision_result["market_lat"] = latest_record.get("market_lat", 10.7905)
    decision_result["market_lng"] = latest_record.get("market_lng", 78.7047)
    decision_result["date"] = latest_date
    decision_result["advice_ta"] = decision_result.get("explanation_ta", "")
    decision_result["advice_en"] = decision_result.get("explanation_en", "")

    _API_CACHE[cache_key] = decision_result
    return decision_result


@app.get("/market-all-crops/{market}")
def get_market_all_crops(market: str):
    """
    Returns all crops and prices for a single district/mandi on the latest date.
    """
    cache_key = f"allcrops_{market.strip().lower()}"
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) as max_date FROM mandi_prices WHERE LOWER(market) = LOWER(?);", (market,))
    row = cursor.fetchone()
    if not row or not row["max_date"]:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No records found for market '{market}'.")
    max_date = row["max_date"]

    query = """
        SELECT p.commodity, p.variety, p.modal_price, p.min_price, p.max_price,
               p.arrivals_tonnes, p.market_lat, p.market_lng, p.district,
               COALESCE(a.pct_change_dod, 0) as pct_change_dod,
               COALESCE(a.is_anomaly, 0) as is_anomaly,
               COALESCE(a.is_confirmed, 0) as is_confirmed,
               COALESCE(a.alert_message, '') as alert_message
        FROM mandi_prices p
        LEFT JOIN anomalies a
            ON p.date = a.date AND LOWER(p.market) = LOWER(a.market) AND LOWER(p.commodity) = LOWER(a.commodity)
        WHERE LOWER(p.market) = LOWER(?) AND p.date = ?
        ORDER BY p.commodity ASC;
    """
    cursor.execute(query, (market, max_date))
    crops = [dict(r) for r in cursor.fetchall()]
    conn.close()

    res = {
        "market": market,
        "date": max_date,
        "crops": crops,
    }
    _API_CACHE[cache_key] = res
    return res


@app.get("/mandi-crops/{market}")
def get_market_all_crops_alias(market: str):
    """Alias for /market-all-crops/{market}."""
    return get_market_all_crops(market)


@app.get("/mandis-map/{commodity}")
def get_mandis_for_map(commodity: str):
    """
    Returns all Tamil Nadu mandis with lat/lng and latest price for a commodity.
    """
    cache_key = f"map_{commodity.strip().lower()}"
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) as max_date FROM mandi_prices WHERE LOWER(commodity) = LOWER(?);", (commodity,))
    row = cursor.fetchone()
    if not row or not row["max_date"]:
        conn.close()
        return []
    max_date = row["max_date"]

    query = """
        SELECT p.market, p.district, p.commodity, p.modal_price, p.market_lat, p.market_lng,
               COALESCE(a.pct_change_dod, 0) as pct_change_dod,
               COALESCE(a.is_confirmed, 0) as is_confirmed
        FROM mandi_prices p
        LEFT JOIN anomalies a
            ON p.date = a.date AND LOWER(p.market) = LOWER(a.market) AND LOWER(p.commodity) = LOWER(a.commodity)
        WHERE LOWER(p.commodity) = LOWER(?) AND p.date = ?;
    """
    cursor.execute(query, (commodity, max_date))
    mandis = [dict(r) for r in cursor.fetchall()]
    conn.close()
    _API_CACHE[cache_key] = mandis
    return mandis


@app.get("/prices/{commodity}/{market}")
def get_price_history(commodity: str, market: str):
    """
    Returns historical price series with trend line and anomaly flags for a (commodity, market) pair.
    """
    cache_key = f"prices_{commodity.strip().lower()}_{market.strip().lower()}"
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]

    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT 
            p.date, p.state, p.district, p.market, p.commodity, p.variety,
            p.min_price, p.max_price, p.modal_price, p.arrivals_tonnes,
            p.market_lat, p.market_lng,
            COALESCE(a.trend, p.modal_price) as trend,
            COALESCE(a.seasonal, 0) as seasonal,
            COALESCE(a.residual, 0) as residual,
            COALESCE(a.pct_change_dod, 0) as pct_change_dod,
            COALESCE(a.anomaly_score, 0) as anomaly_score,
            COALESCE(a.is_anomaly, 0) as is_anomaly,
            COALESCE(a.is_confirmed, 0) as is_confirmed,
            COALESCE(a.nearby_markets, '') as nearby_markets,
            COALESCE(a.nearby_median_change, 0) as nearby_median_change,
            COALESCE(a.alert_message, '') as alert_message
        FROM mandi_prices p
        LEFT JOIN anomalies a 
            ON p.date = a.date AND LOWER(p.market) = LOWER(a.market) AND LOWER(p.commodity) = LOWER(a.commodity)
        WHERE LOWER(p.commodity) = LOWER(?) AND LOWER(p.market) = LOWER(?)
        ORDER BY p.date ASC;
    """
    cursor.execute(query, (commodity, market))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No price records found for commodity '{commodity}' in market '{market}'."
        )

    res = [dict(r) for r in rows]
    _API_CACHE[cache_key] = res
    return res


@app.get("/history/{commodity}/{market}")
def get_price_history_alias(commodity: str, market: str):
    """Alias for /prices/{commodity}/{market}."""
    return get_price_history(commodity, market)


@app.get("/forecast/{commodity}/{market}")
def get_commodity_forecast(commodity: str, market: str):
    """
    Returns 7-day forecast projection, bounds, and indicators for (commodity, market).
    """
    cache_key = f"fc_{commodity.strip().lower()}_{market.strip().lower()}"
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]

    conn = get_db_connection()
    cursor = conn.cursor()
    query_history = """
        SELECT p.date, p.market, p.district, p.commodity, p.modal_price, p.market_lat, p.market_lng
        FROM mandi_prices p
        WHERE LOWER(p.commodity) = LOWER(?) AND LOWER(p.market) = LOWER(?)
        ORDER BY p.date ASC;
    """
    cursor.execute(query_history, (commodity, market))
    history_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not history_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for commodity '{commodity}' in market '{market}'."
        )

    df_history = pd.DataFrame(history_rows)
    forecast_list = generate_7day_forecast(df_history, days_ahead=7)

    # Ensure items have forecast_modal_price and projected_price
    for f in forecast_list:
        f["forecast_modal_price"] = f.get("projected_price", 0)

    res = {
        "commodity": commodity,
        "market": market,
        "district": history_rows[-1].get("district", "Tamil Nadu"),
        "latest_date": history_rows[-1].get("date"),
        "latest_price": history_rows[-1].get("modal_price"),
        "forecast": forecast_list,
        "days": len(forecast_list)
    }
    _API_CACHE[cache_key] = res
    return res


@app.get("/alerts/today")
def get_alerts_today():
    """
    Returns all confirmed alerts generated for the latest available date in database.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get latest date in anomalies table
    cursor.execute("SELECT MAX(date) as max_date FROM anomalies WHERE is_confirmed = 1 OR is_anomaly = 1;")
    row = cursor.fetchone()
    latest_date = row["max_date"] if row else None

    if not latest_date:
        # Fallback to latest date in mandi_prices
        cursor.execute("SELECT MAX(date) as max_date FROM mandi_prices;")
        row = cursor.fetchone()
        latest_date = row["max_date"] if row else None

    if not latest_date:
        conn.close()
        return {"date": None, "alerts": []}

    query = """
        SELECT date, market, commodity, variety, modal_price, pct_change_dod,
               anomaly_score, is_anomaly, is_confirmed, nearby_markets,
               nearby_median_change, divergence_pct, alert_message
        FROM anomalies
        WHERE date = ? AND (is_confirmed = 1 OR is_anomaly = 1)
        ORDER BY anomaly_score DESC;
    """
    cursor.execute(query, (latest_date,))
    alerts = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        "date": latest_date,
        "count": len(alerts),
        "alerts": alerts,
    }


@app.get("/alerts/{market}")
def get_alerts_by_market(market: str):
    """
    Returns all historical alerts generated for a specific market.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT date, market, commodity, variety, modal_price, pct_change_dod,
               anomaly_score, is_anomaly, is_confirmed, nearby_markets,
               nearby_median_change, divergence_pct, alert_message
        FROM anomalies
        WHERE LOWER(market) = LOWER(?) AND (is_confirmed = 1 OR is_anomaly = 1)
        ORDER BY date DESC;
    """
    cursor.execute(query, (market,))
    alerts = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        "market": market,
        "count": len(alerts),
        "alerts": alerts,
    }


@app.post("/pipeline/run")
def trigger_pipeline():
    """
    Triggers manual run of the ETL -> Decompose -> Anomaly -> Alert pipeline.
    """
    try:
        df_result = run_pipeline()
        clear_api_cache()
        return {
            "status": "success",
            "message": "Pipeline executed successfully.",
            "total_processed": len(df_result),
            "anomalies_detected": int(df_result["is_anomaly"].sum()) if not df_result.empty else 0,
            "confirmed_alerts": int(df_result["is_confirmed"].sum()) if not df_result.empty else 0,
        }
    except Exception as e:
        logger.error(f"Error running pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pipeline/status")
def get_pipeline_status():
    """Returns database freshness, latest date, and record counts."""
    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) as max_date, COUNT(*) as total_records FROM mandi_prices")
    row = cur.fetchone()
    max_date = row["max_date"] if row else None
    total_records = row["total_records"] if row else 0

    cur.execute("SELECT COUNT(DISTINCT commodity) as crop_count FROM mandi_prices")
    crop_count = cur.fetchone()["crop_count"]

    cur.execute("SELECT COUNT(DISTINCT market) as market_count FROM mandi_prices")
    market_count = cur.fetchone()["market_count"]
    conn.close()

    is_live = (max_date == today_str)

    return {
        "status": "online",
        "today": today_str,
        "latest_date": max_date,
        "is_live_today": is_live,
        "total_records": total_records,
        "crops_count": crop_count,
        "markets_count": market_count,
    }


@app.post("/pipeline/refresh-live")
def trigger_live_refresh():
    """Fast live data refresh: fetches live rates, updates today's records and anomalies in 2-3s."""
    try:
        from etl.fetch_live_real_prices import update_today_live_prices
        result = update_today_live_prices()
        clear_api_cache()
        return result
    except Exception as e:
        logger.error(f"Error during live refresh: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Mount dashboard static files
dashboard_dir = BASE_DIR / "dashboard"
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")
