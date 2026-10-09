CREATE TABLE trial_artifacts (
    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    mime TEXT NOT NULL,
    render_hint TEXT NOT NULL,
    PRIMARY KEY (trial_id, path)
);

CREATE INDEX trial_artifacts_by_digest ON trial_artifacts(sha256);
