import sqlite3

from config import DB_FILE

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.execute(
    """CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        mod_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general'
    )"""
)
_columns = [row[1] for row in db.execute("PRAGMA table_info(warnings)")]
if "category" not in _columns:
    db.execute("ALTER TABLE warnings ADD COLUMN category TEXT NOT NULL DEFAULT 'general'")

db.execute(
    """CREATE TABLE IF NOT EXISTS streak_sends (
        guild_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        PRIMARY KEY (guild_id, sender_id, receiver_id, day)
    )"""
)
db.execute(
    """CREATE TABLE IF NOT EXISTS streak_pairs (
        guild_id INTEGER NOT NULL,
        user_low INTEGER NOT NULL,
        user_high INTEGER NOT NULL,
        streak INTEGER NOT NULL DEFAULT 0,
        last_day TEXT,
        grace_week TEXT,
        PRIMARY KEY (guild_id, user_low, user_high)
    )"""
)
db.commit()