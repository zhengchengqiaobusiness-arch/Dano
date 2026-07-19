-- P9: asset publication is authoritative; lifecycle registration is retried
-- from this durable outbox when the derived lifecycle index is unavailable.
CREATE TABLE IF NOT EXISTS lifecycle_registration_outbox (
    skill_id      TEXT        NOT NULL,
    subsystem     TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    asset_version INT         NOT NULL,
    last_error    TEXT        NOT NULL DEFAULT '',
    attempts      INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, asset_version)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_registration_outbox_updated
    ON lifecycle_registration_outbox (updated_at);
