import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Any

_SCHEMA = Path(__file__).parent / "schema.sql"


class DBManager:
    _instance: "DBManager | None" = None

    @classmethod
    def instance(cls) -> "DBManager":
        if cls._instance is None:
            from config import DB_PATH
            cls._instance = cls(str(DB_PATH))
        return cls._instance

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._ensure_columns()
        self._ensure_scoped_history_tables()
        self._ensure_pet_state_v2_table()
        self._migrate_legacy_pet_state()
        self._ensure_pet_state()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self._conn.commit()

    def _ensure_columns(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(pet_state)").fetchall()
        }
        required = {
            "exp": "REAL DEFAULT 0.0",
            "intimacy": "REAL DEFAULT 0.0",
            "growth_stage": "TEXT DEFAULT 'newborn'",
            "derived_status_tags": "TEXT DEFAULT '[]'",
        }
        for name, definition in required.items():
            if name in existing:
                continue
            self._conn.execute(f"ALTER TABLE pet_state ADD COLUMN {name} {definition}")
        self._conn.commit()

    def _ensure_scoped_history_tables(self) -> None:
        self._ensure_asset_id_column("operation_log")
        self._ensure_asset_id_column("memory_store")
        self._conn.commit()

    def _ensure_asset_id_column(self, table_name: str) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "asset_id" not in existing:
            self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN asset_id TEXT")
        current_asset_id = self._normalize_asset_id(None)
        self._conn.execute(
            f"UPDATE {table_name} SET asset_id=? WHERE asset_id IS NULL OR TRIM(asset_id)=''",
            (current_asset_id,),
        )

    def _ensure_pet_state_v2_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_state_v2 (
                asset_id               TEXT PRIMARY KEY,
                hunger                 REAL    DEFAULT 100.0,
                cleanliness            REAL    DEFAULT 100.0,
                mood                   REAL    DEFAULT 80.0,
                energy                 REAL    DEFAULT 100.0,
                exp                    REAL    DEFAULT 0.0,
                intimacy               REAL    DEFAULT 0.0,
                growth_stage           TEXT    DEFAULT 'newborn',
                derived_status_tags    TEXT    DEFAULT '[]',
                personality_extrovert  REAL    DEFAULT 0.7,
                personality_obedient   REAL    DEFAULT 0.5,
                personality_curious    REAL    DEFAULT 0.8,
                last_active_at         TEXT,
                updated_at             TEXT
            )
            """
        )
        self._conn.commit()

    def _migrate_legacy_pet_state(self) -> None:
        existing = self._conn.execute("SELECT COUNT(*) AS count FROM pet_state_v2").fetchone()
        if existing and int(existing["count"] or 0) > 0:
            return

        legacy = self._conn.execute("SELECT * FROM pet_state WHERE id=1").fetchone()
        if legacy is None:
            return

        from config import CURRENT_PET_ID

        fields = dict(legacy)
        fields.pop("id", None)
        fields["asset_id"] = str(CURRENT_PET_ID or "default")
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        self._conn.execute(
            f"INSERT OR IGNORE INTO pet_state_v2 ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )
        self._conn.commit()

    def _ensure_pet_state(self, asset_id: str | None = None) -> None:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        exists = self._conn.execute(
            "SELECT asset_id FROM pet_state_v2 WHERE asset_id=?",
            (normalized_asset_id,),
        ).fetchone()
        if exists:
            return
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO pet_state_v2 (asset_id, last_active_at, updated_at) VALUES (?,?,?)",
            (normalized_asset_id, now, now),
        )
        self._conn.commit()

    @staticmethod
    def _normalize_asset_id(asset_id: object | None) -> str:
        from config import CURRENT_PET_ID

        text = str(asset_id or CURRENT_PET_ID or "default").strip().lower().replace(" ", "_").replace("-", "_")
        return text or "default"

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def get_pet_state(self, asset_id: str | None = None) -> dict:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        self._ensure_pet_state(normalized_asset_id)
        row = self._conn.execute(
            "SELECT * FROM pet_state_v2 WHERE asset_id=?",
            (normalized_asset_id,),
        ).fetchone()
        return dict(row)

    def update_pet_state(self, fields: dict[str, Any], asset_id: str | None = None) -> None:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        self._ensure_pet_state(normalized_asset_id)
        fields["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values())
        values.append(normalized_asset_id)
        self._conn.execute(
            f"UPDATE pet_state_v2 SET {set_clause} WHERE asset_id=?",
            values,
        )
        self._conn.commit()

    def log_operation(self, action_type: str, detail: dict | None = None, asset_id: str | None = None) -> None:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        self._conn.execute(
            "INSERT INTO operation_log (asset_id, action_type, detail) VALUES (?,?,?)",
            (normalized_asset_id, action_type, json.dumps(detail or {}))
        )
        self._conn.commit()

    def add_memory(self, summary: str, importance: float = 0.5, asset_id: str | None = None) -> None:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        self._conn.execute(
            "INSERT INTO memory_store (asset_id, summary, importance) VALUES (?,?,?)",
            (normalized_asset_id, summary, importance)
        )
        self._conn.commit()

    def get_recent_memories(self, limit: int = 5, asset_id: str | None = None) -> list[dict]:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        rows = self._conn.execute(
            "SELECT summary, importance, created_at FROM memory_store "
            "WHERE asset_id=? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (normalized_asset_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_memories(self, limit: int = 5, asset_id: str | None = None) -> list[dict]:
        normalized_asset_id = self._normalize_asset_id(asset_id)
        rows = self._conn.execute(
            "SELECT summary, importance, created_at FROM memory_store "
            "WHERE asset_id=? ORDER BY created_at DESC LIMIT ?",
            (normalized_asset_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
