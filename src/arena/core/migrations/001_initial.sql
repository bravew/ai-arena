CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE trials (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    contestant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    flags_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, contestant_id, task_id, attempt)
);

CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    trial_id TEXT REFERENCES trials(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    model_asked TEXT NOT NULL,
    model_served TEXT,
    status INTEGER,
    error_class TEXT,
    tokens_json TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, seq)
);

CREATE TABLE scores (
    id INTEGER PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    scorer_id TEXT NOT NULL,
    scorer_version TEXT NOT NULL,
    value REAL NOT NULL,
    normalized REAL NOT NULL CHECK (normalized >= 0 AND normalized <= 1),
    passed INTEGER,
    rationale TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (trial_id, scorer_id, scorer_version)
);

CREATE INDEX calls_by_trial ON calls(trial_id, seq);
CREATE INDEX scores_by_trial ON scores(trial_id);
