"""Local request telemetry; failures must never alter classifier verdicts."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from src.config import DATA_DIR

DB_PATH = DATA_DIR / "firewall_analytics.db"


def _connect():
    return sqlite3.connect(DB_PATH, timeout=30)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
                prompt TEXT, status TEXT, reason TEXT, score REAL,
                latency_ms REAL, category TEXT
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        if "category" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN category TEXT")


def log_request(prompt, status, reason, score, latency_ms, category=None):
    with closing(_connect()) as conn, conn:
        cursor = conn.execute("""
            INSERT INTO requests (timestamp, prompt, status, reason, score, latency_ms, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), prompt, status,
              str(reason or ""), score, latency_ms, category))
        request_id = cursor.lastrowid
    return request_id


def get_recent_requests(limit=30):
    """Read the latest local audit records for the operator activity view."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Activity limit must be between 1 and 100")
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, timestamp, prompt, status, reason, category, score, latency_ms
            FROM requests ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]


def get_analytics_stats():
    with closing(_connect()) as conn:
        total, blocked, allowed, latency = conn.execute("""
            SELECT COUNT(*), SUM(status = 'BLOCK'), SUM(status = 'ALLOW'), AVG(latency_ms)
            FROM requests
        """).fetchone()
        categories = [
            {"category": category, "count": count}
            for category, count in conn.execute("""
                SELECT COALESCE(category, 'unknown'), COUNT(*)
                FROM requests WHERE status = 'BLOCK'
                GROUP BY COALESCE(category, 'unknown')
                ORDER BY COUNT(*) DESC LIMIT 5
            """)
        ]
    return {"total_requests":total, "blocked_requests":blocked or 0,
            "allowed_requests":allowed or 0, "average_latency_ms":round(latency or 0.0,2),
            "top_attack_categories":categories}


init_db()
