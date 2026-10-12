import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DB_PATH = PROCESSED_DATA_DIR / "mandi_prices.db"
MANDI_COORDS_PATH = BASE_DIR / "config" / "mandi_coords.json"

# Create directories if they don't exist
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Agmarknet API Config
AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_BASE_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"
AGMARKNET_API_KEY = os.getenv("AGMARKNET_API_KEY", "579b464db66ec23bdd0000013a1d8e83438744f16d6d012c4ae76dd3")
STATE_FILTER = "Tamil Nadu"

# Decomposition Settings
STL_PERIOD_DAYS = 7          # Seasonal period (e.g., 7-day weekly cycle)
MIN_HISTORY_POINTS = 14     # Minimum datapoints needed for STL decomposition

# Anomaly Detection Settings
DEFAULT_ANOMALY_THRESHOLD = 0.65  # Combined score threshold for flagging anomaly

# Per-crop specific anomaly thresholds (can be customized based on commodity volatility)
CROP_ANOMALY_THRESHOLDS = {
    "tomato": 0.60,      # Tomato is highly volatile, lower threshold to catch spikes
    "onion": 0.62,       # Onion is moderately volatile
    "potato": 0.65,
    "rice": 0.70,        # Rice prices are stable, higher threshold to avoid false alarms
    "banana": 0.65,
    "brinjal": 0.60,
    "green chilli": 0.60,
}

# Cross-Market Spatial Comparator Settings
CROSS_MARKET_NEIGHBOR_COUNT = 5       # Max nearest mandis to compare against
MAX_NEIGHBOR_DISTANCE_KM = 250.0      # Maximum search radius for nearby mandis
CROSS_MARKET_DIVERGENCE_THRESHOLD = 15.0 # Divergence threshold in % vs neighbor median to confirm anomaly

# Default TN centroid fallback coordinates (e.g. Tiruchirappalli)
DEFAULT_TN_LAT = 10.7905
DEFAULT_TN_LNG = 78.7047
