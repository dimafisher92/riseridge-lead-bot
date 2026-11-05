"""Which places a live run already handled (spec 3.3, filter 4)."""

import datetime as dt
import os
import sqlite3


FAILED = "failed"


class SeenStore:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (place_id TEXT PRIMARY KEY, "
                        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_outcome TEXT)")

    def recently_seen(self, place_id, today, days):
        row = self.db.execute("SELECT last_seen, last_outcome FROM seen WHERE place_id = ?",
                              (place_id,)).fetchone()
        if not row or row[1] == FAILED:  # a failed push is a retry record: collect it again
            return False
        return (today - dt.date.fromisoformat(row[0])).days < days

    def last_outcome(self, place_id):
        row = self.db.execute("SELECT last_outcome FROM seen WHERE place_id = ?",
                              (place_id,)).fetchone()
        return row[0] if row else None

    def mark(self, place_id, today, outcome):
        day = today.isoformat()
        self.db.execute(
            "INSERT INTO seen VALUES (?, ?, ?, ?) ON CONFLICT(place_id) DO UPDATE SET "
            "last_seen = excluded.last_seen, last_outcome = excluded.last_outcome",
            (place_id, day, day, outcome))
        self.db.commit()

    def close(self):
        self.db.close()
