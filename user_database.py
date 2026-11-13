import json
import sqlite3
from pathlib import Path


class UserDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_algorithm TEXT NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    telegram_username TEXT NOT NULL DEFAULT '',
                    company_name TEXT NOT NULL DEFAULT '',
                    company_description TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    channel_label TEXT NOT NULL DEFAULT '',
                    registered_at TEXT NOT NULL DEFAULT ''
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS channel_posts (
                    telegram_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    published_at TEXT NOT NULL,
                    text_preview TEXT NOT NULL,
                    reactions_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (channel_id, message_id),
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )
            """)

    def get(self, telegram_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
            return dict(row) if row else None

    def delete_account(self, telegram_id):
        with self.connect() as connection:
            connection.execute("DELETE FROM channel_posts WHERE telegram_id = ?", (telegram_id,))
            connection.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))

    def email_exists(self, email, telegram_id=None):
        query = "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE"
        params = [email.strip().casefold()]
        if telegram_id is not None:
            query += " AND telegram_id != ?"
            params.append(str(telegram_id))
        with self.connect() as connection:
            return connection.execute(query, params).fetchone() is not None

    def save(self, telegram_id, email, credential, **profile):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO users (
                    telegram_id, email, password_salt, password_hash,
                    password_algorithm, password_iterations, display_name,
                    telegram_username, company_name, company_description,
                    channel_id, channel_label, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    email = excluded.email,
                    password_salt = CASE WHEN excluded.password_hash = '' THEN users.password_salt ELSE excluded.password_salt END,
                    password_hash = CASE WHEN excluded.password_hash = '' THEN users.password_hash ELSE excluded.password_hash END,
                    password_algorithm = CASE WHEN excluded.password_hash = '' THEN users.password_algorithm ELSE excluded.password_algorithm END,
                    password_iterations = CASE WHEN excluded.password_hash = '' THEN users.password_iterations ELSE excluded.password_iterations END,
                    display_name = excluded.display_name,
                    telegram_username = excluded.telegram_username,
                    company_name = excluded.company_name,
                    company_description = excluded.company_description,
                    channel_id = excluded.channel_id,
                    channel_label = excluded.channel_label,
                    registered_at = excluded.registered_at
            """, (
                str(telegram_id), email.strip().casefold(), credential.get("salt", ""),
                credential.get("hash", ""), credential.get("algorithm", "pbkdf2_sha256"),
                credential.get("iterations", 600_000), profile.get("display_name", ""),
                profile.get("telegram_username", ""), profile.get("company_name", ""),
                profile.get("company_description", ""), profile.get("channel_id", ""),
                profile.get("channel_label", ""), profile.get("registered_at", ""),
            ))

    def migrate_accounts_json(self, legacy_path: Path):
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        with self.connect() as connection:
            for telegram_id, account in legacy.items():
                if not account.get("email") or not account.get("credential"):
                    continue
                connection.execute("""
                    INSERT OR IGNORE INTO users (
                        telegram_id, email, password_salt, password_hash,
                        password_algorithm, password_iterations
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(telegram_id), account["email"].strip().casefold(),
                    account["credential"].get("salt", ""), account["credential"].get("hash", ""),
                    account["credential"].get("algorithm", "pbkdf2_sha256"),
                    account["credential"].get("iterations", 600_000),
                ))

    def record_post(self, telegram_id, channel_id, message_id, published_at, text_preview):
        with self.connect() as connection:
            connection.execute("""
                INSERT OR IGNORE INTO channel_posts
                (telegram_id, channel_id, message_id, published_at, text_preview)
                VALUES (?, ?, ?, ?, ?)
            """, (str(telegram_id), str(channel_id), int(message_id), published_at, text_preview[:180]))

    def update_post_reactions(self, channel_id, message_id, reactions):
        value = json.dumps(reactions, ensure_ascii=False)
        with self.connect() as connection:
            connection.execute("""
                UPDATE channel_posts SET reactions_json = ?
                WHERE channel_id = ? AND message_id = ?
            """, (value, str(channel_id), int(message_id)))

    def analytics(self, telegram_id, limit=10):
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT message_id, published_at, text_preview, reactions_json
                FROM channel_posts WHERE telegram_id = ?
                ORDER BY published_at DESC LIMIT ?
            """, (str(telegram_id), int(limit))).fetchall()
            total_posts = connection.execute(
                "SELECT COUNT(*) FROM channel_posts WHERE telegram_id = ?", (str(telegram_id),)
            ).fetchone()[0]
        posts = []
        for row in rows:
            reactions = json.loads(row["reactions_json"] or "[]")
            posts.append({**dict(row), "reactions": reactions, "reaction_total": sum(item.get("total_count", 0) for item in reactions)})
        return {"total_posts": total_posts, "posts": posts}
