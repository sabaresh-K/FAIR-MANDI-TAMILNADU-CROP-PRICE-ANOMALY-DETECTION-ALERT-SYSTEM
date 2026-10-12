"""
ETL Ingestion Module: fetch_agmarknet.py
Fetches daily commodity price data from the data.gov.in Agmarknet API resource.
Filters results to state=Tamil Nadu, handles pagination, rate limits, and missing fields,
and saves raw JSON responses to /data/raw.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests

from config.config import (
    AGMARKNET_BASE_URL,
    AGMARKNET_API_KEY,
    STATE_FILTER,
    RAW_DATA_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AgmarknetFetcher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or AGMARKNET_API_KEY
        self.base_url = AGMARKNET_BASE_URL
        self.state_filter = STATE_FILTER

    def fetch_records(self, limit: int = 1000, max_pages: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch records from data.gov.in API with pagination and error handling.
        """
        if not self.api_key:
            logger.warning(
                "AGMARKNET_API_KEY environment variable is not set. "
                "Returning empty list. (Synthetic data generator can be used for testing)."
            )
            return []

        all_records = []
        offset = 0

        for page in range(max_pages):
            params = {
                "api-key": self.api_key,
                "format": "json",
                "offset": offset,
                "limit": limit,
                "filters[state]": self.state_filter,
            }

            logger.info(f"Fetching Agmarknet page {page + 1} (offset={offset}, limit={limit})...")
            
            # Retry mechanism for government API server latency
            response = None
            for attempt in range(2):
                try:
                    response = requests.get(self.base_url, params=params, timeout=8)
                    if response.status_code == 429:
                        logger.warning("Rate limit hit (429). Sleeping for 3 seconds...")
                        time.sleep(3)
                        continue
                    if response.status_code == 200:
                        break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as req_err:
                    logger.warning(f"Attempt {attempt + 1} timed out or failed: {req_err}. Retrying in 1s...")
                    time.sleep(1)

            if not response or response.status_code != 200:
                logger.error(f"Failed to fetch page {page + 1} after retries. Status code: {response.status_code if response else 'No Response'}")
                break

            try:
                data = response.json()

                records = data.get("records", [])
                if not records:
                    logger.info("No more records found.")
                    break

                all_records.extend(records)
                logger.info(f"Fetched {len(records)} records from page {page + 1}.")

                total_records = data.get("total", len(all_records))
                if offset + len(records) >= total_records:
                    logger.info("Reached end of total records reported by API.")
                    break

                offset += limit
                time.sleep(0.5)  # Respect rate limits

            except requests.exceptions.RequestException as e:
                logger.error(f"HTTP Error fetching data from Agmarknet API: {e}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                break

        if all_records:
            self._save_raw_json(all_records)

        return all_records

    def _save_raw_json(self, records: List[Dict[str, Any]]) -> str:
        """Saves raw JSON payload to data/raw directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = RAW_DATA_DIR / f"agmarknet_raw_{timestamp}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(records)} raw records to {filename}")
            return str(filename)
        except Exception as e:
            logger.error(f"Error saving raw JSON file: {e}")
            return ""


if __name__ == "__main__":
    fetcher = AgmarknetFetcher()
    records = fetcher.fetch_records(limit=100)
    print(f"Total fetched records: {len(records)}")
