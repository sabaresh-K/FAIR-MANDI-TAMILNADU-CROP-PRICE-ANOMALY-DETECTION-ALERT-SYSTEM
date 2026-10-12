# Tamil Nadu Crop Price Anomaly & Alert System 🌾
## Uzhavan Sandhai AI (உழவன் சந்தை AI)

> 📖 **Comprehensive Project Documentation**: For the exhaustive problem statement, mathematical formulations, algorithmic approach, and system architecture, see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md).

An intelligent time-series anomaly detection and early warning alert system for Tamil Nadu agricultural market (mandi) prices. The system ingests daily Agmarknet commodity data, applies STL time-series decomposition and Isolation Forest machine learning, performs spatial cross-mandi verification using Haversine distance, and generates plain-language actionable alerts for farmers.

---

## 📁 Project Structure

```text
.
├── config/
│   ├── config.py             # System configurations & crop anomaly thresholds
│   └── mandi_coords.json     # Static lat/lng lookup table for TN mandis
├── data/
│   ├── raw/                  # Daily raw API response dumps (JSON)
│   └── processed/            # Cleaned SQLite DB (mandi_prices.db)
├── etl/
│   ├── fetch_agmarknet.py    # Agmarknet API fetcher & pagination logic
│   └── clean.py              # Data cleaning, unit standardization & coordinate enrichment
├── models/
│   ├── decompose.py          # STL time-series decomposition (statsmodels)
│   ├── anomaly.py            # Multi-factor anomaly detector (Z-score + IQR + Isolation Forest)
│   ├── cross_market.py       # Haversine distance spatial cross-mandi comparator
│   └── alert_text.py         # Plain-language alert message generator
├── api/
│   └── main.py               # FastAPI backend with REST endpoints & APScheduler daily job
├── dashboard/
│   └── index.html            # Interactive HTML5/JS + Chart.js dashboard
├── notebooks/
│   └── eda_and_tuning.ipynb  # EDA and threshold tuning Jupyter notebook
├── tests/
│   ├── test_anomaly.py       # Unit tests for anomaly detection
│   ├── test_cross_market.py  # Unit tests for spatial comparator
│   └── test_etl.py           # Unit tests for ETL cleaning & formatting
├── generate_synthetic_data.py # Helper script to seed synthetic TN mandi data for testing/demo
├── run_pipeline.py           # CLI runner for the end-to-end processing pipeline
├── requirements.txt          # Python dependencies
└── README.md                 # Documentation
```

---

## 🚀 Quick Start Guide

### 1. Environment Setup

Clone the repository and install dependencies:

```bash
# Create and activate a Python virtual environment (optional but recommended)
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Unix/macOS:
source venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 2. Seed Data (Offline / Testing)

To test the system immediately without an API key, seed synthetic Tamil Nadu mandi price data (90 days of history across 10 mandis and 5 major crops, complete with injected price anomalies):

```bash
python generate_synthetic_data.py
```

### 3. Run the Processing Pipeline

Execute the end-to-end ETL $\rightarrow$ STL Decomposition $\rightarrow$ Anomaly Detection $\rightarrow$ Spatial Consensus $\rightarrow$ Alert Generation pipeline:

```bash
python run_pipeline.py
```

*(Note: If `AGMARKNET_API_KEY` is set in your environment, `fetch_agmarknet.py` will attempt to fetch live daily prices from data.gov.in resource `9ef84268-d588-465a-a308-a864a43d0070`.)*

---

## 🧪 Running Unit Tests

Run the test suite with `pytest`:

```bash
pytest tests/ -v
```

All unit tests verify:
- **STL Decomposition & Statistical Outliers**: Z-score, IQR, and Isolation Forest anomaly scoring on synthetic price series with injected spikes and drops.
- **Cross-Mandi Spatial Comparator**: Haversine distance calculations and confirmation logic (ensuring isolated mandi anomalies are confirmed while regional market shifts are correctly filtered).
- **ETL Cleaning & Parsing**: Date formatting, string standardization, and coordinate enrichment.

---

## 🌐 FastAPI Backend & Interactive Dashboard

Start the FastAPI server using Uvicorn:

```bash
uvicorn api.main:app --reload --port 8000
```

### Available Endpoints:
- **Interactive Dashboard**: `http://localhost:8000/dashboard/index.html` (or `http://localhost:8000/dashboard/`)
- **API Documentation (Swagger UI)**: `http://localhost:8000/docs`
- **GET `/prices/{commodity}/{market}`**: Price history with STL trend line and anomaly flags.
- **GET `/alerts/today`**: Confirmed alerts generated for the latest available date.
- **GET `/alerts/{market}`**: Historical alerts for a specific mandi.
- **GET `/commodities`**: List of all unique commodities in the database.
- **GET `/markets`**: List of all unique Tamil Nadu mandis in the database.
- **POST `/pipeline/run`**: Trigger manual pipeline execution.

---

## ⚙️ Configuration & Threshold Tuning

Custom crop anomaly sensitivity thresholds and spatial parameters can be modified in `config/config.py`:

```python
# Per-crop anomaly thresholds (0.0 to 1.0)
CROP_ANOMALY_THRESHOLDS = {
    "tomato": 0.60,      # High volatility
    "onion": 0.62,
    "potato": 0.65,
    "rice": 0.70,        # Low volatility
    "banana": 0.65,
}

# Cross-Market Spatial Comparator Settings
CROSS_MARKET_NEIGHBOR_COUNT = 5       # Max nearest mandis to compare
MAX_NEIGHBOR_DISTANCE_KM = 250.0      # Search radius in KM
CROSS_MARKET_DIVERGENCE_THRESHOLD = 15.0 # Divergence threshold in % vs neighbor median
```

---

## 📊 Live Agmarknet Integration

To ingest live data from the Indian Government Agmarknet API:
1. Register on [data.gov.in](https://data.gov.in) and request an API key for resource `9ef84268-d588-465a-a308-a864a43d0070`.
2. Set the environment variable:
   - Windows PowerShell: `$env:AGMARKNET_API_KEY="your_api_key_here"`
   - Unix/macOS: `export AGMARKNET_API_KEY="your_api_key_here"`
3. Run `python run_pipeline.py`.
