"""
Authentication & Farmer Profile Module: api/auth.py
Provides secure registration, login, session management, and profile settings
tailored for Tamil Nadu farmers with SQLite WAL persistence.
"""

import sqlite3
import hashlib
import secrets
import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from pathlib import Path

from pydantic import BaseModel, Field

from config.config import DB_PATH

logger = logging.getLogger(__name__)

# Constants
SALT_BYTES = 16
HASH_ITERATIONS = 100000
TOKEN_VALIDITY_DAYS = 30


# Pydantic Schemas
class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=4, max_length=50)
    district: str = Field(default="Madurai")
    preferred_mandi: str = Field(default="Madurai")
    preferred_crops: List[str] = Field(default=["Tomato"])
    notification_channel: str = Field(default="sms") # Dedicated cellular SMS alert


class LoginRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=4, max_length=50)


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    district: Optional[str] = None
    preferred_mandi: Optional[str] = None
    preferred_crops: Optional[List[str]] = None
    notification_channel: Optional[str] = None
    alert_active: Optional[bool] = None


def get_db_connection() -> sqlite3.Connection:
    """Returns SQLite connection with WAL mode and busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Generates PBKDF2 hash of password with random 16-byte hex salt."""
    if not salt:
        salt = secrets.token_hex(SALT_BYTES)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        bytes.fromhex(salt),
        HASH_ITERATIONS
    )
    return key.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """Verifies password against PBKDF2 hash using constant-time comparison."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, expected_hash)


def clean_phone(phone_str: str) -> str:
    """Strips non-digit characters and normalizes 10-digit Indian numbers."""
    digits = ''.join(c for c in phone_str if c.isdigit())
    if digits.startswith('91') and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]
    return digits


def init_auth_tables():
    """Initializes users and user_sessions tables and seeds demo farmers."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        district TEXT NOT NULL,
        preferred_mandi TEXT NOT NULL,
        preferred_crops TEXT NOT NULL,
        notification_channel TEXT DEFAULT 'sms',
        alert_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        last_login TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token);")

    # Ensure no predefined demo accounts exist in the database
    cursor.execute("DELETE FROM users WHERE phone IN ('9876543210', '9842109876');")
    cursor.execute("DELETE FROM user_sessions WHERE phone IN ('9876543210', '9842109876');")
    conn.commit()
    conn.close()


def register_user(req: RegisterRequest) -> Dict[str, Any]:
    """Registers a new farmer user and creates an active session."""
    phone = clean_phone(req.phone)
    if len(phone) != 10:
        raise ValueError("Invalid phone number. Must be a valid 10-digit mobile number.")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE phone = ?;", (phone,))
    if cursor.fetchone():
        conn.close()
        raise ValueError("Mobile number is already registered. Please log in.")

    pwd_hash, salt = hash_password(req.password)
    now = datetime.now().isoformat()
    crops_json = json.dumps(req.preferred_crops if req.preferred_crops else ["Tomato"])

    cursor.execute("""
    INSERT INTO users (full_name, phone, password_hash, salt, district, preferred_mandi, preferred_crops, notification_channel, alert_active, created_at, last_login)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?);
    """, (
        req.full_name.strip(),
        phone,
        pwd_hash,
        salt,
        req.district.strip(),
        req.preferred_mandi.strip(),
        crops_json,
        req.notification_channel,
        now,
        now
    ))
    user_id = cursor.lastrowid

    # Create session
    token = secrets.token_hex(32)
    expires_at = (datetime.now() + timedelta(days=TOKEN_VALIDITY_DAYS)).isoformat()
    cursor.execute("""
    INSERT INTO user_sessions (token, user_id, phone, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?);
    """, (token, user_id, phone, now, expires_at))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_id,
            "full_name": req.full_name.strip(),
            "phone": phone,
            "district": req.district.strip(),
            "preferred_mandi": req.preferred_mandi.strip(),
            "preferred_crops": req.preferred_crops if req.preferred_crops else ["Tomato"],
            "notification_channel": req.notification_channel,
            "alert_active": True
        }
    }


def login_user(req: LoginRequest) -> Dict[str, Any]:
    """Authenticates farmer with phone and password/PIN, returning active session token."""
    phone = clean_phone(req.phone)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE phone = ?;", (phone,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError("Mobile number not found. Please check or register first.")

    user = dict(row)
    if not verify_password(req.password, user["salt"], user["password_hash"]):
        conn.close()
        raise ValueError("Incorrect password or PIN. Please try again.")

    # Update last login
    now = datetime.now().isoformat()
    cursor.execute("UPDATE users SET last_login = ? WHERE id = ?;", (now, user["id"]))

    # Create new session token
    token = secrets.token_hex(32)
    expires_at = (datetime.now() + timedelta(days=TOKEN_VALIDITY_DAYS)).isoformat()
    cursor.execute("""
    INSERT INTO user_sessions (token, user_id, phone, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?);
    """, (token, user["id"], phone, now, expires_at))

    conn.commit()
    conn.close()

    try:
        crops = json.loads(user["preferred_crops"])
    except Exception:
        crops = [user["preferred_crops"]]

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "phone": user["phone"],
            "district": user["district"],
            "preferred_mandi": user["preferred_mandi"],
            "preferred_crops": crops,
            "notification_channel": user["notification_channel"],
            "alert_active": bool(user["alert_active"]),
            "last_login": now
        }
    }


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Retrieves user profile for a valid, non-expired session token."""
    if not token:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute("""
    SELECT s.user_id, s.expires_at, u.*
    FROM user_sessions s
    JOIN users u ON s.user_id = u.id
    WHERE s.token = ? AND s.expires_at > ?;
    """, (token, now))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    user = dict(row)
    try:
        crops = json.loads(user["preferred_crops"])
    except Exception:
        crops = [user["preferred_crops"]]

    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "phone": user["phone"],
        "district": user["district"],
        "preferred_mandi": user["preferred_mandi"],
        "preferred_crops": crops,
        "notification_channel": user["notification_channel"],
        "alert_active": bool(user["alert_active"]),
        "created_at": user["created_at"],
        "last_login": user["last_login"]
    }


def logout_user(token: str) -> bool:
    """Removes active session token."""
    if not token:
        return True
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_sessions WHERE token = ?;", (token,))
    conn.commit()
    conn.close()
    return True


def update_user_profile(user_id: int, req: ProfileUpdateRequest) -> Dict[str, Any]:
    """Updates user preferences and profile details."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError("User not found.")

    updates = []
    params = []

    if req.full_name is not None:
        updates.append("full_name = ?")
        params.append(req.full_name.strip())
    if req.district is not None:
        updates.append("district = ?")
        params.append(req.district.strip())
    if req.preferred_mandi is not None:
        updates.append("preferred_mandi = ?")
        params.append(req.preferred_mandi.strip())
    if req.preferred_crops is not None:
        updates.append("preferred_crops = ?")
        params.append(json.dumps(req.preferred_crops))
    if req.notification_channel is not None:
        updates.append("notification_channel = ?")
        params.append(req.notification_channel)
    if req.alert_active is not None:
        updates.append("alert_active = ?")
        params.append(1 if req.alert_active else 0)

    if updates:
        params.append(user_id)
        sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ?;"
        cursor.execute(sql, tuple(params))
        conn.commit()

    cursor.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
    updated_row = cursor.fetchone()
    conn.close()

    user = dict(updated_row)
    try:
        crops = json.loads(user["preferred_crops"])
    except Exception:
        crops = [user["preferred_crops"]]

    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "phone": user["phone"],
        "district": user["district"],
        "preferred_mandi": user["preferred_mandi"],
        "preferred_crops": crops,
        "notification_channel": user["notification_channel"],
        "alert_active": bool(user["alert_active"])
    }


def get_database_auth_status() -> Dict[str, Any]:
    """Returns database connection status and real registered user count directly from SQLite."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as user_count FROM users;")
    user_count = cursor.fetchone()["user_count"]
    cursor.execute("SELECT COUNT(*) as session_count FROM user_sessions;")
    session_count = cursor.fetchone()["session_count"]
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r["name"] for r in cursor.fetchall()]
    conn.close()

    return {
        "status": "connected",
        "database_file": str(DB_PATH.name),
        "database_path": str(DB_PATH),
        "registered_users": user_count,
        "active_sessions": session_count,
        "tables": tables
    }

