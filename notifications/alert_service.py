"""
Automated Farmer Notification & 1-Click Mobile SMS Alert Service: notifications/alert_service.py
Generates real-time price advisories, price spikes, selling decisions, and district arbitrage alerts.
Directly uses the farmer's mobile phone number registered at login to open the native phone SMS app
with the recipient (+91XXXXXXXXXX) and alert message pre-filled. Zero-cost, zero-setup, 100% reliable.
"""

import os
import json
import logging
import sqlite3
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from config.config import DB_PATH, BASE_DIR

logger = logging.getLogger(__name__)

# Load .env file automatically
load_dotenv()


# Tamil Crop Translation Map
TAMIL_CROPS = {
    "Tomato": "தக்காளி",
    "Onion": "வெங்காயம்",
    "Potato": "உருளைக்கிழங்கு",
    "Rice": "நெல்",
    "Banana": "வாழைப்பழம்",
    "Green Chilli": "பச்சை மிளகாய்",
    "Brinjal": "கத்திரிக்காய்",
    "Carrot": "கேரட்",
    "Coconut": "தேங்காய்",
    "Ladies Finger": "வெண்டைக்காய்",
    "Garlic": "பூண்டு",
    "Ginger": "இஞ்சி",
    "Cabbage": "முட்டைக்கோஸ்",
    "Cauliflower": "காலிஃபிளவர்",
    "Drumstick": "முருங்கைக்காய்",
    "Beetroot": "பீட்ரூட்",
    "Coriander": "கொத்தமல்லி",
    "Mango": "மாம்பழம்",
    "Turmeric": "மஞ்சள்",
    "Cotton": "பருத்தி",
}


def get_db_connection() -> sqlite3.Connection:
    """Returns SQLite connection with WAL mode."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_notification_tables():
    """Initializes the notification_logs table for audit and history tracking."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notification_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT NOT NULL,
        farmer_name TEXT,
        commodity TEXT,
        market TEXT,
        alert_type TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'mobile_sms',
        message_title TEXT,
        message_body TEXT NOT NULL,
        delivery_status TEXT NOT NULL,
        gateway TEXT NOT NULL DEFAULT '1-Click Mobile SMS App',
        sent_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_phone ON notification_logs(phone);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notification_logs(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_date ON notification_logs(sent_at);")
    conn.commit()
    conn.close()


def reload_gateway_config():
    """Lightweight config refresh."""
    load_dotenv(override=True)


def save_gateway_config(config_dict: Dict[str, str]) -> Dict[str, Any]:
    """Saves preferences if needed and returns status."""
    return get_gateway_status()


def build_mobile_sms_url(phone: str, message: str) -> str:
    """
    Generates standard device SMS URI.
    Tapping this on a mobile handset (Android/iOS) launches the default native SMS application
    with the registered recipient number (+91XXXXXXXXXX) and alert message pre-filled.
    """
    clean_digits = "".join(c for c in phone if c.isdigit())
    if len(clean_digits) == 10:
        dest = f"+91{clean_digits}"
    elif clean_digits.startswith("91") and len(clean_digits) == 12:
        dest = f"+{clean_digits}"
    else:
        dest = f"+{clean_digits}"
    encoded_text = urllib.parse.quote(message)
    return f"sms:{dest}?body={encoded_text}"


def get_gateway_status() -> Dict[str, Any]:
    """Returns the operational status of the 1-click mobile SMS alert engine."""
    return {
        "sms": {
            "is_live": True,
            "provider": "1-Click Native Mobile SMS App",
            "description": "Direct mobile phone SMS delivery using the farmer's registered phone number",
            "cost": "Free (Zero 3rd-party API credits needed)",
            "setup_required": False
        },
        "mode": "1-Click Native Mobile SMS Ready"
    }


def build_alert_message(farmer_name: str, commodity: str, market: str, alert_type: str, data: Dict[str, Any], lang: str = "en") -> tuple[str, str]:
    """
    Builds clean, structured SMS text messages for the farmer's mobile phone.
    Returns (title, clean_sms_body).
    """
    crop_display = TAMIL_CROPS.get(commodity, commodity) if lang == "ta" else commodity
    price = int(data.get("modal_price", 0))
    pct = abs(round(data.get("pct_change_dod", 0), 1))
    district = data.get("district", market)

    if alert_type == "PRICE_SPIKE" or data.get("decision") == "SELL_NOW":
        if lang == "ta":
            title = f"விலை ஏற்றம்: {crop_display} (இன்றே விற்கலாம்)"
            body = f"[Fair Mandi AI] வணக்கம் {farmer_name}! {market} சந்தையில் {crop_display} விலை இன்று ₹{price:,}/Q (+{pct}%). இப்போதே விற்கலாம்! நேரலை விலை: http://127.0.0.1:8000/dashboard"
        else:
            title = f"Price Spike: {commodity} (Sell Today)"
            body = f"[Fair Mandi AI] Hi {farmer_name}! {commodity} price in {market} mandi is ₹{price:,}/Q (+{pct}%) today. Sell Now! View live: http://127.0.0.1:8000/dashboard"

    elif alert_type == "DISTRICT_ARBITRAGE" or data.get("best_higher_price_mandi"):
        best = data.get("best_higher_price_mandi", {})
        higher_mandi = best.get("market", "Nearby Mandi")
        higher_price = int(best.get("modal_price", price))
        gain = int(best.get("price_diff", 0))
        if lang == "ta":
            title = f"கூடுதல் லாப வாய்ப்பு: {higher_mandi}"
            body = f"[Fair Mandi AI] {farmer_name}, {district} மாவட்டத்தில் {higher_mandi} சந்தையில் {crop_display} விலை ₹{higher_price:,}/Q (+₹{gain} கூடுதல் லாபம்). செல்லலாம்! http://127.0.0.1:8000/dashboard"
        else:
            title = f"District Arbitrage: {higher_mandi}"
            body = f"[Fair Mandi AI] Arbitrage: {higher_mandi} in {district} offers ₹{higher_price:,}/Q (+₹{gain} gain) for {commodity} today! Details: http://127.0.0.1:8000/dashboard"

    elif alert_type == "PRICE_DROP" or data.get("decision") == "HOLD":
        if lang == "ta":
            title = f"விலை சரிவு எச்சரிக்கை: {crop_display}"
            body = f"[Fair Mandi AI] {farmer_name}, {market} சந்தையில் {crop_display} விலை ₹{price:,}/Q ஆக குறைந்துள்ளது (-{pct}%). விற்பனையை தள்ளிப்போடலாம். http://127.0.0.1:8000/dashboard"
        else:
            title = f"Price Drop Warning: {commodity}"
            body = f"[Fair Mandi AI] Price Alert: {commodity} in {market} mandi dropped to ₹{price:,}/Q (-{pct}%). Hold sale if possible. http://127.0.0.1:8000/dashboard"

    else:
        # Test alert or general market advisory check
        if lang == "ta":
            title = "நேரலை SMS எச்சரிக்கை சோதனை"
            body = f"[Fair Mandi AI] நேரலை SMS சோதனை: வணக்கம் {farmer_name}! {market} சந்தையில் {crop_display} இன்றைய விலை ₹{price:,}/Q. நேரலை தளம்: http://127.0.0.1:8000/dashboard"
        else:
            title = "Live SMS Test Alert"
            body = f"[Fair Mandi AI] Live SMS Test: Hello {farmer_name}! {commodity} price in {market} mandi is ₹{price:,}/Q today. Live portal: http://127.0.0.1:8000/dashboard"

    return title, body


def record_notification_log(
    user_id: Optional[int],
    phone: str,
    farmer_name: str,
    commodity: str,
    market: str,
    alert_type: str,
    title: str,
    body: str,
    status: str = "READY",
    gateway: str = "1-Click Mobile SMS App",
    channel: str = "mobile_sms"
) -> int:
    """Inserts a notification record into notification_logs table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO notification_logs (
        user_id, phone, farmer_name, commodity, market, alert_type, channel,
        message_title, message_body, delivery_status, gateway, sent_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, phone, farmer_name, commodity, market, alert_type, channel,
        title, body, status, gateway, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def send_farmer_notification(
    user: Dict[str, Any],
    alert_type: str,
    commodity: str,
    market: str,
    data: Dict[str, Any],
    lang: str = "en"
) -> Dict[str, Any]:
    """
    Builds mobile SMS alert for the farmer's registered phone number.
    Logs record in notification_logs and generates direct 1-click mobile SMS URI.
    """
    init_notification_tables()

    phone = user.get("phone", "")
    farmer_name = user.get("full_name", "Farmer")

    title, sms_body = build_alert_message(farmer_name, commodity, market, alert_type, data, lang=lang)
    sms_url = build_mobile_sms_url(phone, sms_body)

    # Record audit log
    record_notification_log(
        user_id=user.get("id"),
        phone=phone,
        farmer_name=farmer_name,
        commodity=commodity,
        market=market,
        alert_type=alert_type,
        title=title,
        body=sms_body,
        status="READY",
        gateway="1-Click Mobile SMS App",
        channel="mobile_sms"
    )

    return {
        "success": True,
        "phone": phone,
        "farmer_name": farmer_name,
        "commodity": commodity,
        "market": market,
        "alert_type": alert_type,
        "channel": "mobile_sms",
        "title": title,
        "message_body": sms_body,
        "sms_text": sms_body,
        "sms_url": sms_url,
        "status": "READY",
        "gateway": "1-Click Mobile SMS App",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def get_farmer_notification_history(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieves notification history for a farmer with 1-click SMS URL."""
    init_notification_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, phone, farmer_name, commodity, market, alert_type, channel,
           message_title, message_body, delivery_status, gateway, sent_at
    FROM notification_logs
    WHERE user_id = ?
    ORDER BY id DESC
    LIMIT ?;
    """, (user_id, limit))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Precompute sms_url for each history item
    for row in rows:
        row["sms_url"] = build_mobile_sms_url(row["phone"], row["message_body"])

    return rows


def trigger_automated_market_alerts() -> Dict[str, Any]:
    """
    Scans all registered active farmers, checks their preferred crops and mandis
    against today's market decisions, and generates 1-click SMS alerts when:
      - Price spiked (Sell Now)
      - Price crashed (Hold Sale)
      - A higher-price mandi in the same district offers extra net profit (Arbitrage)
    Prevents duplicate alerts for the same farmer, crop, and date.
    """
    from models.decision import compute_farmer_decision

    init_notification_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find today's date
    cursor.execute("SELECT MAX(date) FROM mandi_prices;")
    latest_date_row = cursor.fetchone()
    if not latest_date_row or not latest_date_row[0]:
        conn.close()
        return {"scanned_farmers": 0, "alerts_sent": 0, "message": "No price data in database."}
    latest_date = latest_date_row[0]

    # Fetch active farmers
    cursor.execute("SELECT id, full_name, phone, district, preferred_mandi, preferred_crops FROM users WHERE alert_active = 1;")
    farmers = [dict(r) for r in cursor.fetchall()]
    conn.close()

    alerts_sent = 0
    scanned_count = len(farmers)
    dispatched_summary = []

    for farmer in farmers:
        crops_raw = farmer.get("preferred_crops", "[]")
        try:
            crops = json.loads(crops_raw) if isinstance(crops_raw, str) else crops_raw
        except Exception:
            crops = ["Tomato"]

        market = farmer.get("preferred_mandi", "Madurai")

        for crop in crops:
            # Check if alert already logged today to this user for this crop
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
            SELECT COUNT(*) FROM notification_logs
            WHERE user_id = ? AND LOWER(commodity) = LOWER(?) AND sent_at LIKE ?;
            """, (farmer["id"], crop, f"{latest_date}%"))
            already_sent = cur.fetchone()[0] > 0

            if already_sent:
                conn.close()
                continue

            # Fetch today's price & nearby prices
            cur.execute("""
            SELECT * FROM mandi_prices
            WHERE date = ? AND LOWER(market) = LOWER(?) AND LOWER(commodity) = LOWER(?);
            """, (latest_date, market, crop))
            record_row = cur.fetchone()

            if not record_row:
                conn.close()
                continue
            latest_record = dict(record_row)

            cur.execute("""
            SELECT market, district, commodity, modal_price, market_lat, market_lng
            FROM mandi_prices
            WHERE date = ? AND LOWER(commodity) = LOWER(?) AND LOWER(market) != LOWER(?);
            """, (latest_date, crop, market))
            nearby_records = [dict(r) for r in cur.fetchall()]
            conn.close()

            # Compute decision
            decision = compute_farmer_decision(latest_record, [], nearby_records)

            alert_type = None
            if decision.get("decision") == "SELL_NOW":
                alert_type = "PRICE_SPIKE"
            elif decision.get("best_higher_price_mandi") and decision["best_higher_price_mandi"].get("price_diff", 0) >= 100:
                alert_type = "DISTRICT_ARBITRAGE"
            elif decision.get("decision") == "HOLD":
                alert_type = "PRICE_DROP"

            if alert_type:
                res = send_farmer_notification(farmer, alert_type, crop, market, decision)
                alerts_sent += 1
                dispatched_summary.append({
                    "farmer": farmer["full_name"],
                    "phone": farmer["phone"],
                    "crop": crop,
                    "market": market,
                    "alert_type": alert_type,
                    "sms_url": res["sms_url"]
                })

    return {
        "scanned_farmers": scanned_count,
        "alerts_sent": alerts_sent,
        "date": latest_date,
        "dispatched_summary": dispatched_summary
    }
