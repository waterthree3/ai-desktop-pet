CREATE TABLE IF NOT EXISTS pet_state (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
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
);

CREATE TABLE IF NOT EXISTS operation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT    DEFAULT 'default',
    action_type TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS memory_store (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT    DEFAULT 'default',
    summary     TEXT    NOT NULL,
    importance  REAL    DEFAULT 0.5,
    created_at  TEXT    DEFAULT (datetime('now', 'localtime'))
);
