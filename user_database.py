import hashlib
import hmac
import json
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path


class UserDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(channel_posts)")}
            if "reactions_received" not in columns:
                connection.execute("ALTER TABLE channel_posts ADD COLUMN reactions_received INTEGER NOT NULL DEFAULT 0")
                connection.execute("UPDATE channel_posts SET reactions_received = 1 WHERE reactions_json != '[]'")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS discussion_threads (
                    group_id TEXT NOT NULL,
                    root_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    post_id INTEGER NOT NULL,
                    PRIMARY KEY (group_id, root_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS discussion_comments (
                    group_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    root_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT 0,
                    quality TEXT NOT NULL DEFAULT 'unknown',
                    PRIMARY KEY (group_id, message_id)
                )
            """)
            comment_columns = {row["name"] for row in connection.execute("PRAGMA table_info(discussion_comments)")}
            if "created_at" not in comment_columns:
                connection.execute("ALTER TABLE discussion_comments ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
            if "quality" not in comment_columns:
                connection.execute("ALTER TABLE discussion_comments ADD COLUMN quality TEXT NOT NULL DEFAULT 'unknown'")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reaction_samples (
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    sampled_at INTEGER NOT NULL,
                    reactions_json TEXT NOT NULL,
                    PRIMARY KEY (channel_id, message_id, sampled_at)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS channel_member_samples (
                    channel_id TEXT NOT NULL,
                    sampled_at INTEGER NOT NULL,
                    member_count INTEGER NOT NULL,
                    PRIMARY KEY (channel_id, sampled_at)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS discussion_comments_root ON discussion_comments(group_id, root_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS discussion_threads_post ON discussion_threads(channel_id, post_id)")

    def get(self, telegram_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
            return dict(row) if row else None

    def delete_account(self, telegram_id):
        with self.connect() as connection:
            connection.execute("""
                DELETE FROM discussion_comments WHERE (group_id, root_id) IN (
                    SELECT t.group_id, t.root_id FROM discussion_threads t JOIN channel_posts p
                    ON p.channel_id = t.channel_id AND p.message_id = t.post_id WHERE p.telegram_id = ?
                )
            """, (telegram_id,))
            connection.execute("""
                DELETE FROM discussion_threads WHERE (channel_id, post_id) IN (
                    SELECT channel_id, message_id FROM channel_posts WHERE telegram_id = ?
                )
            """, (telegram_id,))
            connection.execute("""
                DELETE FROM reaction_samples WHERE (channel_id, message_id) IN (
                    SELECT channel_id, message_id FROM channel_posts WHERE telegram_id = ?
                )
            """, (telegram_id,))
            connection.execute("""
                DELETE FROM channel_member_samples WHERE channel_id NOT IN (
                    SELECT DISTINCT channel_id FROM channel_posts
                )
            """)
            connection.execute("DELETE FROM channel_posts WHERE telegram_id = ?", (telegram_id,))
            connection.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))

    def authenticate(self, email, password):
        if not isinstance(email, str) or len(email) > 254 or not isinstance(password, str) or not 12 <= len(password.strip()) <= 128:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT telegram_id, password_hash, password_salt, password_iterations FROM users WHERE email = ?",
                (email.strip().casefold(),),
            ).fetchone()
        if not row or not row["password_hash"]:
            return None
        digest = hashlib.pbkdf2_hmac("sha256", password.strip().encode("utf-8"), bytes.fromhex(row["password_salt"]), row["password_iterations"])
        if hmac.compare_digest(digest.hex(), row["password_hash"]):
            return row["telegram_id"]
        return None

    def update_company(self, telegram_id, name, description):
        with self.connect() as connection:
            connection.execute(
                "UPDATE users SET company_name = ?, company_description = ? WHERE telegram_id = ?",
                (name, description, telegram_id),
            )

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

    @staticmethod
    def find_post_channel(connection, channel, message_id):
        channel_id = str(channel["id"])
        username = channel.get("username")
        alias = f"@{username}" if username else channel_id
        row = connection.execute("""
            SELECT channel_id FROM channel_posts
            WHERE message_id = ? AND (channel_id = ? OR lower(channel_id) = lower(?))
            ORDER BY channel_id = ? DESC LIMIT 1
        """, (message_id, channel_id, alias, channel_id)).fetchone()
        return row["channel_id"] if row else None

    def update_post_reactions(self, channel, message_id, reactions, sampled_at=None):
        value = json.dumps(reactions, ensure_ascii=False)
        sampled_at = int(sampled_at or datetime.now(timezone.utc).timestamp())
        with self.connect() as connection:
            channel_id = self.find_post_channel(connection, channel, message_id)
            if channel_id is None:
                return
            connection.execute("""
                UPDATE channel_posts SET reactions_json = ?, reactions_received = 1
                WHERE channel_id = ? AND message_id = ?
            """, (value, str(channel_id), int(message_id)))
            connection.execute("""
                INSERT INTO reaction_samples (channel_id, message_id, sampled_at, reactions_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id, sampled_at) DO UPDATE SET reactions_json = excluded.reactions_json
            """, (str(channel_id), int(message_id), sampled_at, value))

    def tracked_channels(self):
        with self.connect() as connection:
            return [row["channel_id"] for row in connection.execute("SELECT DISTINCT channel_id FROM channel_posts")]

    def sample_channel_members(self, channel_id, member_count, sampled_at=None):
        with self.connect() as connection:
            connection.execute("""
                INSERT OR IGNORE INTO channel_member_samples (channel_id, sampled_at, member_count)
                VALUES (?, ?, ?)
            """, (str(channel_id), int(sampled_at or datetime.now(timezone.utc).timestamp()), int(member_count)))

    def latest_member_sample_at(self, channel_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(sampled_at) AS sampled_at FROM channel_member_samples WHERE channel_id = ?",
                (str(channel_id),),
            ).fetchone()
            return row["sampled_at"] if row else None

    def observe_discussion(self, message):
        chat = message["chat"]
        if chat["type"] != "supergroup":
            return
        automatic = message.get("is_automatic_forward")
        reply = message.get("reply_to_message") or {}
        thread_id = message.get("message_thread_id")
        if not (automatic or reply or thread_id):
            return
        group_id = str(chat["id"])
        message_id = message["message_id"]
        content = (message.get("text") or message.get("caption") or "").strip()
        words = re.findall(r"[^\W_]+", content, flags=re.UNICODE)
        urls = len(re.findall(r"(?:https?://|t\.me/|www\.)", content, flags=re.IGNORECASE))
        repeated = bool(re.search(r"(.)\1{7,}", content, flags=re.DOTALL))
        if urls >= 3 or repeated:
            quality = "spam"
        elif sum(any(char.isalpha() for char in word) for word in words) >= 3 and len(content) >= 24:
            quality = "substantive"
        else:
            quality = "short"
        root = message if automatic else reply
        origin = root.get("forward_origin") or {}
        with self.connect() as connection:
            if root.get("is_automatic_forward") and origin.get("type") == "channel":
                post_id = origin["message_id"]
                channel_id = self.find_post_channel(connection, origin["chat"], post_id)
                if channel_id is None:
                    return
                root_id = root["message_id"]
                connection.execute("""
                    INSERT OR IGNORE INTO discussion_threads (group_id, root_id, channel_id, post_id)
                    VALUES (?, ?, ?, ?)
                """, (group_id, root_id, channel_id, post_id))
            if automatic:
                return
            if message.get("from", {}).get("is_bot"):
                return
            reply_id = reply.get("message_id")
            connection.execute("""
                INSERT OR IGNORE INTO discussion_comments (group_id, message_id, root_id, created_at, quality)
                SELECT group_id, ?, root_id, ?, ? FROM discussion_threads
                WHERE group_id = ? AND root_id != ?
                AND (root_id IN (?, ?) OR root_id = (
                    SELECT root_id FROM discussion_comments WHERE group_id = ? AND message_id = ?
                ))
                ORDER BY root_id = ? DESC LIMIT 1
            """, (message_id, int(message.get("date", 0)), quality, group_id, message_id, thread_id, reply_id, group_id, reply_id, thread_id))

    def analytics(self, telegram_id, limit=10):
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT p.message_id, p.published_at, p.text_preview, p.reactions_json, p.reactions_received,
                    (SELECT COUNT(*) FROM discussion_threads t JOIN discussion_comments c
                     ON c.group_id = t.group_id AND c.root_id = t.root_id
                     WHERE t.channel_id = p.channel_id AND t.post_id = p.message_id) AS comment_total,
                    p.channel_id,
                    (SELECT MIN(c.created_at) FROM discussion_threads t JOIN discussion_comments c
                     ON c.group_id = t.group_id AND c.root_id = t.root_id
                     WHERE t.channel_id = p.channel_id AND t.post_id = p.message_id AND c.created_at > 0) AS first_comment_at,
                    (SELECT COUNT(*) FROM discussion_threads t JOIN discussion_comments c
                     ON c.group_id = t.group_id AND c.root_id = t.root_id
                     WHERE t.channel_id = p.channel_id AND t.post_id = p.message_id AND c.quality = 'substantive') AS substantive_comments,
                    (SELECT COUNT(*) FROM discussion_threads t JOIN discussion_comments c
                     ON c.group_id = t.group_id AND c.root_id = t.root_id
                     WHERE t.channel_id = p.channel_id AND t.post_id = p.message_id AND c.quality = 'short') AS short_comments,
                    (SELECT COUNT(*) FROM discussion_threads t JOIN discussion_comments c
                     ON c.group_id = t.group_id AND c.root_id = t.root_id
                     WHERE t.channel_id = p.channel_id AND t.post_id = p.message_id AND c.quality = 'spam') AS spam_comments
                FROM channel_posts p WHERE p.telegram_id = ?
                ORDER BY p.published_at DESC LIMIT ?
            """, (str(telegram_id), int(limit))).fetchall()
            total_posts = connection.execute(
                "SELECT COUNT(*) FROM channel_posts WHERE telegram_id = ?", (str(telegram_id),)
            ).fetchone()[0]
        posts = []
        for row in rows:
            reactions = json.loads(row["reactions_json"] or "[]")
            post = dict(row)
            del post["reactions_json"]
            channel_id = post.pop("channel_id")
            post["reactions"] = reactions
            post["reaction_total"] = sum(item.get("total_count", 0) for item in reactions) if post.pop("reactions_received") else None
            post["classified_comments"] = post["substantive_comments"] + post["short_comments"] + post["spam_comments"]
            post["substantive_comment_pct"] = round(post["substantive_comments"] / post["classified_comments"] * 100, 1) if post["classified_comments"] else None
            post["spam_comment_pct"] = round(post["spam_comments"] / post["classified_comments"] * 100, 1) if post["classified_comments"] else None
            published_at = int(datetime.fromisoformat(post["published_at"]).timestamp())
            post["first_comment_seconds"] = max(0, int(post["first_comment_at"]) - published_at) if post["first_comment_at"] else None
            post.pop("first_comment_at")
            with self.connect() as connection:
                post["reaction_samples"] = [
                    {"at": row["sampled_at"], "reactions": json.loads(row["reactions_json"])}
                    for row in connection.execute("""
                        SELECT sampled_at, reactions_json FROM reaction_samples
                        WHERE channel_id = ? AND message_id = ? ORDER BY sampled_at
                    """, (channel_id, post["message_id"]))
                ]
                post["member_samples"] = [
                    {"at": row["sampled_at"], "members": row["member_count"]}
                    for row in connection.execute("""
                        SELECT sampled_at, member_count FROM channel_member_samples
                        WHERE channel_id = ? ORDER BY sampled_at
                    """, (channel_id,))
                ]
            reactions_by_time = [
                {"at": sample["at"], "total": sum(int(item.get("total_count", 0)) for item in sample["reactions"])}
                for sample in post["reaction_samples"]
            ]
            observed = [sample for sample in reactions_by_time if sample["total"] > 0]
            final_total = post["reaction_total"]
            post["first_reaction_seconds"] = max(0, observed[0]["at"] - published_at) if observed else None
            post["reaction_velocity_15m"] = self._observed_window(reactions_by_time, published_at, 15 * 60)
            post["reaction_velocity_60m"] = self._observed_window(reactions_by_time, published_at, 60 * 60)
            post["peak_after_seconds"] = None
            post["half_life_seconds"] = None
            post["long_tail_reactions"] = None
            post["possible_spike"] = False
            if final_total is not None and reactions_by_time:
                deltas = []
                previous = 0
                for sample in reactions_by_time:
                    increase = max(0, sample["total"] - previous)
                    if sample["at"] >= published_at:
                        deltas.append((increase, sample["at"]))
                    previous = sample["total"]
                if deltas and max(deltas)[0] > 0:
                    post["peak_after_seconds"] = max(deltas)[1] - published_at
                    largest_jump = max(increase for increase, _ in deltas)
                    post["possible_spike"] = largest_jump >= max(5, final_total * 0.5)
                half = next((sample for sample in reactions_by_time if sample["total"] >= final_total / 2), None)
                if half:
                    post["half_life_seconds"] = max(0, half["at"] - published_at)
                baseline = next((sample for sample in reversed(reactions_by_time) if sample["at"] < published_at + 24 * 3600), None)
                after_day = next((sample for sample in reversed(reactions_by_time) if sample["at"] >= published_at + 24 * 3600), None)
                if baseline and after_day:
                    post["long_tail_reactions"] = max(0, after_day["total"] - baseline["total"])
            member_samples = post["member_samples"]
            before = [sample for sample in member_samples if sample["at"] <= published_at]
            baseline_members = before[-1] if before else None
            post["subscribers_at_publish"] = baseline_members["members"] if baseline_members else None
            post["subscribers_delta_1h"] = self._member_delta(member_samples, published_at, baseline_members, 3600)
            post["subscribers_delta_24h"] = self._member_delta(member_samples, published_at, baseline_members, 86400)
            post["subscribers_latest"] = member_samples[-1]["members"] if member_samples else None
            denominator = baseline_members["members"] if baseline_members else None
            post["reaction_rate_pct"] = round(post["reaction_total"] / denominator * 100, 2) if post["reaction_total"] is not None and denominator else None
            post["comment_rate_pct"] = round(post["comment_total"] / denominator * 100, 2) if denominator else None
            post.pop("reaction_samples")
            post.pop("member_samples")
            posts.append(post)
        return {"total_posts": total_posts, "posts": posts}

    @staticmethod
    def _observed_window(samples, published_at, seconds):
        end = published_at + seconds
        closest = next((sample for sample in reversed(samples) if published_at <= sample["at"] <= end), None)
        return closest["total"] if closest else None

    @staticmethod
    def _member_delta(samples, published_at, baseline, seconds):
        if baseline is None:
            return None
        target = published_at + seconds
        sample = min((item for item in samples if abs(item["at"] - target) <= 20 * 60), key=lambda item: abs(item["at"] - target), default=None)
        return sample["members"] - baseline["members"] if sample else None
