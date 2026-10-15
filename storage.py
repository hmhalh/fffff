import hashlib
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_id TEXT NOT NULL,
            speaker_name TEXT NOT NULL, text TEXT NOT NULL, digest TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
            scope TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at INTEGER NOT NULL
        );
        """)
        self.db.commit()

    def get_setting(self, key, default=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, str(value)))
        self.db.commit()

    def recent_messages(self, limit=12):
        rows = self.db.execute("SELECT speaker_id,speaker_name,text FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def is_duplicate(self, text):
        normalized = " ".join(text.lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        row = self.db.execute("SELECT 1 FROM messages WHERE digest=? AND created_at > ? LIMIT 1",
                              (digest, int(time.time()) - 86400)).fetchone()
        return row is not None

    def add_message(self, speaker_id, speaker_name, text):
        normalized = " ".join(text.lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        self.db.execute("INSERT INTO messages(speaker_id,speaker_name,text,digest,created_at) VALUES(?,?,?,?,?)",
                        (speaker_id, speaker_name, text, digest, int(time.time())))
        self.db.commit()

    def get_memory(self, scope):
        row = self.db.execute("SELECT text FROM memories WHERE scope=?", (scope,)).fetchone()
        return row["text"] if row else ""

    def append_memory(self, scope, addition):
        old = self.get_memory(scope)
        # MVP memory is a rolling transcript excerpt; replace with summarization for long-term use.
        combined = (old + "\n" + addition).strip()
        combined = "\n".join(combined.splitlines()[-30:])
        self.db.execute("INSERT INTO memories(scope,text,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(scope) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
                        (scope, combined, int(time.time())))
        self.db.commit()
