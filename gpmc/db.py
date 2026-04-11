import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Self

from .models import MediaItem


class Storage:
    # Database schema version - increment when schema changes
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self._migrate()
        self._create_tables()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.conn.close()

    def _get_schema_version(self) -> int:
        """Get current schema version from database."""
        cursor = self.conn.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'
        """)
        if not cursor.fetchone():
            return 1  # No version table = original schema
        cursor = self.conn.execute("SELECT version FROM schema_version WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else 1

    def _set_schema_version(self, version: int) -> None:
        """Set schema version in database."""
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER
        )
        """)
        self.conn.execute(
            """
        INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)
        """,
            (version,),
        )
        self.conn.commit()

    def _migrate(self) -> None:
        """Run migrations if needed."""
        current_version = self._get_schema_version()

        if current_version < 2:
            self._migrate_v1_to_v2()

        if current_version < self.SCHEMA_VERSION:
            self._set_schema_version(self.SCHEMA_VERSION)

    def _migrate_v1_to_v2(self) -> None:
        """
        Migration v1 -> v2: Rename state_token/page_token to sync_token/resume_token.

        SQLite doesn't support RENAME COLUMN in older versions, so we:
        1. Create new table with new column names
        2. Copy data
        3. Drop old table
        4. Rename new table
        """
        # Check if state table exists first
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='state'")
        if not cursor.fetchone():
            return  # No state table yet, nothing to migrate

        # Check if migration is needed (old column names exist)
        cursor = self.conn.execute("PRAGMA table_info(state)")
        columns = {row[1] for row in cursor.fetchall()}

        if "state_token" in columns:
            # Already has old column names, migrate to new
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS state_new (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sync_token TEXT,
                    resume_token TEXT,
                    init_complete INTEGER
                );
                INSERT OR REPLACE INTO state_new (id, sync_token, resume_token, init_complete)
                SELECT id, state_token, page_token, init_complete FROM state;
                DROP TABLE state;
                ALTER TABLE state_new RENAME TO state;
            """)
            self.conn.commit()

    def _create_tables(self) -> None:
        """Create the remote_media table if it doesn't exist."""
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS remote_media (
            media_key TEXT PRIMARY KEY,
            file_name TEXT,
            dedup_key TEXT,
            is_canonical BOOL,
            type INTEGER,
            caption TEXT,
            collection_id TEXT,
            size_bytes INTEGER,
            quota_charged_bytes INTEGER,
            origin TEXT,
            content_version INTEGER,
            utc_timestamp INTEGER,
            server_creation_timestamp INTEGER,
            timezone_offset INTEGER,
            width INTEGER,
            height INTEGER,
            remote_url TEXT,
            upload_status INTEGER,
            trash_timestamp INTEGER,
            is_archived INTEGER,
            is_favorite INTEGER,
            is_locked INTEGER,
            is_original_quality INTEGER,
            latitude REAL,
            longitude REAL,
            location_name TEXT,
            location_id TEXT,
            is_edited INTEGER,
            make TEXT,
            model TEXT,
            aperture REAL,
            shutter_speed REAL,
            iso INTEGER,
            focal_length REAL,
            duration INTEGER,
            capture_frame_rate REAL,
            encoded_frame_rate REAL,
            is_micro_video INTEGER,
            micro_video_width INTEGER,
            micro_video_height INTEGER
        )
        """)

        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sync_token TEXT,
            resume_token TEXT,
            init_complete INTEGER
        )
        """)

        self.conn.execute("""
        INSERT OR IGNORE INTO state (id, sync_token, resume_token, init_complete)
        VALUES (1, '', '', 0)
        """)
        self.conn.commit()

    def update(self, items: Iterable[MediaItem]) -> None:
        """Insert or update multiple MediaItems in the database."""
        if not items:
            return

        # Convert dataclass objects to dictionaries
        items_dicts = [asdict(item) for item in items]

        # Prepare the SQL statement with all fields
        columns = items_dicts[0].keys()
        placeholders = ", ".join("?" * len(columns))
        columns_str = ", ".join(columns)
        updates = ", ".join(f"{col}=excluded.{col}" for col in columns if col != "media_key")

        sql = f"""
        INSERT INTO remote_media ({columns_str})
        VALUES ({placeholders})
        ON CONFLICT(media_key) DO UPDATE SET {updates}
        """

        # Prepare the values for each item
        values = [tuple(item[col] for col in columns) for item in items_dicts]

        # Execute in a transaction
        with self.conn:
            self.conn.executemany(sql, values)

    def delete(self, media_keys: Sequence[str]) -> None:
        """
        Delete multiple rows by their media_key.

        Args:
            media_keys: A sequence of media_key values to delete
        """
        if not media_keys:
            return

        # Create a temporary table with the keys to delete
        sql = """
        DELETE FROM remote_media
        WHERE media_key IN ({})
        """.format(",".join(["?"] * len(media_keys)))

        # Execute in a transaction
        with self.conn:
            self.conn.execute(sql, media_keys)

    def get_sync_tokens(self) -> tuple[str, str]:
        """
        Get both sync tokens as a tuple (sync_token, resume_token).

        Based on Google Photos app token naming:
        - sync_token: Current sync state token (CURRENT_SYNC/NEXT_SYNC in app)
        - resume_token: Pagination token for resuming sync (INITIAL_RESUME/DELTA_RESUME in app)

        Returns ('', '') if no tokens are stored.
        """
        cursor = self.conn.execute("""
        SELECT sync_token, resume_token FROM state WHERE id = 1
        """)
        return cursor.fetchone() or ("", "")

    def update_sync_tokens(self, sync_token: str | None = None, resume_token: str | None = None) -> None:
        """
        Update one or both sync tokens.
        Pass None to leave a token unchanged.

        Based on Google Photos app token naming:
        - sync_token: Current sync state token (CURRENT_SYNC/NEXT_SYNC in app)
        - resume_token: Pagination token for resuming sync (INITIAL_RESUME/DELTA_RESUME in app)
        """
        updates = []
        params = []

        if sync_token is not None:
            updates.append("sync_token = ?")
            params.append(sync_token)
        if resume_token is not None:
            updates.append("resume_token = ?")
            params.append(resume_token)

        if updates:
            sql = f"UPDATE state SET {', '.join(updates)} WHERE id = 1"
            with self.conn:
                self.conn.execute(sql, params)

    def get_init_state(self) -> bool:
        """ """
        cursor = self.conn.execute("""
        SELECT init_complete FROM state WHERE id = 1
        """)
        return cursor.fetchone()[0] or False

    def set_init_state(self, state: int) -> None:
        """ """
        with self.conn:
            self.conn.execute("UPDATE state SET init_complete = ? WHERE id = 1", (state,))

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
