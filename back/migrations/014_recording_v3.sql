-- Recording V3 persistence.  Facts and revisions are tenant-scoped and facts
-- are append-only.  Pi session history is metadata/evidence, not an LLM cache.

CREATE TABLE IF NOT EXISTS recording_sessions (
    tenant                  TEXT        NOT NULL,
    recording_id            TEXT        NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'created'
        CHECK (status IN (
            'created','recording','compiling','draft','reviewing',
            'published','failed','closed'
        )),
    base_url                TEXT        NOT NULL DEFAULT '',
    current_revision        INTEGER     NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
    browser_lease_until     TIMESTAMPTZ,
    resume_token_hash       TEXT,
    metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, recording_id),
    UNIQUE (recording_id)
);
CREATE INDEX IF NOT EXISTS idx_recording_sessions_tenant_status
    ON recording_sessions (tenant, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS recording_facts (
    tenant          TEXT        NOT NULL,
    recording_id    TEXT        NOT NULL,
    fact_id         TEXT        NOT NULL UNIQUE,
    sequence        BIGINT      NOT NULL CHECK (sequence >= 0),
    kind            TEXT        NOT NULL
        CHECK (kind IN (
            'action','page','dom_control','dom_mutation','request','response',
            'request_failed','script','diagnostic'
        )),
    observed_at     TIMESTAMPTZ NOT NULL,
    action_id       TEXT,
    page_id         TEXT,
    data            JSONB       NOT NULL,
    content_hash    TEXT        NOT NULL,
    PRIMARY KEY (tenant, recording_id, fact_id),
    UNIQUE (tenant, recording_id, sequence),
    FOREIGN KEY (tenant, recording_id)
        REFERENCES recording_sessions (tenant, recording_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_recording_facts_scope_sequence
    ON recording_facts (tenant, recording_id, sequence);
CREATE INDEX IF NOT EXISTS idx_recording_facts_action
    ON recording_facts (tenant, recording_id, action_id) WHERE action_id IS NOT NULL;

-- Keep reruns/upgrades compatible with databases created before DOM mutation
-- evidence became a first-class immutable fact.
ALTER TABLE recording_facts
    DROP CONSTRAINT IF EXISTS recording_facts_kind_check;
ALTER TABLE recording_facts
    ADD CONSTRAINT recording_facts_kind_check CHECK (kind IN (
        'action','page','dom_control','dom_mutation','request','response',
        'request_failed','script','diagnostic'
    ));

CREATE OR REPLACE FUNCTION reject_recording_fact_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'recording_facts are immutable; append a new fact instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_facts_immutable ON recording_facts;
CREATE TRIGGER recording_facts_immutable
BEFORE UPDATE OR DELETE ON recording_facts
FOR EACH ROW EXECUTE FUNCTION reject_recording_fact_mutation();

CREATE TABLE IF NOT EXISTS recording_revisions (
    tenant              TEXT        NOT NULL,
    recording_id        TEXT        NOT NULL,
    revision            INTEGER     NOT NULL CHECK (revision >= 1),
    parent_revision     INTEGER     NOT NULL CHECK (parent_revision >= 0),
    content_hash        TEXT        NOT NULL,
    snapshot            JSONB       NOT NULL,
    actor               TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, recording_id, revision),
    FOREIGN KEY (tenant, recording_id)
        REFERENCES recording_sessions (tenant, recording_id) ON DELETE RESTRICT,
    CHECK (revision = parent_revision + 1)
);

CREATE OR REPLACE FUNCTION reject_recording_revision_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'recording_revisions are immutable; append a new revision instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_revisions_immutable ON recording_revisions;
CREATE TRIGGER recording_revisions_immutable
BEFORE UPDATE OR DELETE ON recording_revisions
FOR EACH ROW EXECUTE FUNCTION reject_recording_revision_mutation();

-- operation_id is globally unique.  Reusing it with another tenant, recording,
-- kind or request_hash is an idempotency conflict, never a second operation.
CREATE TABLE IF NOT EXISTS recording_operations (
    operation_id        TEXT        PRIMARY KEY,
    tenant              TEXT        NOT NULL,
    recording_id        TEXT        NOT NULL,
    kind                TEXT        NOT NULL,
    request_hash        TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'started'
        CHECK (status IN ('started','completed','failed')),
    result              JSONB,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant, recording_id)
        REFERENCES recording_sessions (tenant, recording_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_recording_operations_scope
    ON recording_operations (tenant, recording_id, created_at DESC);

-- Idempotency identity and terminal results are immutable at the database
-- boundary as well as in the repository compare-and-set implementation.
CREATE OR REPLACE FUNCTION enforce_recording_operation_transition()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'recording_operations are durable idempotency records';
    END IF;
    IF NEW.operation_id IS DISTINCT FROM OLD.operation_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.recording_id IS DISTINCT FROM OLD.recording_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'recording operation identity is immutable';
    END IF;
    IF OLD.status <> 'started' THEN
        RAISE EXCEPTION 'terminal recording operation cannot be rewritten';
    END IF;
    IF NEW.status NOT IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'recording operation must transition directly to a terminal state';
    END IF;
    IF NEW.status = 'completed' AND NEW.error IS NOT NULL THEN
        RAISE EXCEPTION 'completed recording operation cannot contain an error';
    END IF;
    IF NEW.status = 'failed' AND (NEW.error IS NULL OR NEW.result IS NOT NULL) THEN
        RAISE EXCEPTION 'failed recording operation requires only an error';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_operations_transition ON recording_operations;
CREATE TRIGGER recording_operations_transition
BEFORE UPDATE OR DELETE ON recording_operations
FOR EACH ROW EXECUTE FUNCTION enforce_recording_operation_transition();

CREATE TABLE IF NOT EXISTS recording_pi_sessions (
    tenant              TEXT        NOT NULL,
    recording_id        TEXT        NOT NULL,
    pi_session_id       TEXT        NOT NULL,
    role                TEXT        NOT NULL
        CHECK (role IN ('planner','acceptance','security','compliance')),
    model_id            TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','running','idle','failed','closed')),
    last_revision       INTEGER     NOT NULL DEFAULT 0 CHECK (last_revision >= 0),
    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, recording_id, pi_session_id),
    UNIQUE (pi_session_id),
    FOREIGN KEY (tenant, recording_id)
        REFERENCES recording_sessions (tenant, recording_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_recording_pi_sessions_role
    ON recording_pi_sessions (tenant, recording_id, role, updated_at DESC);

CREATE OR REPLACE FUNCTION enforce_recording_pi_session_identity()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'recording_pi_sessions are durable session identities';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.recording_id IS DISTINCT FROM OLD.recording_id
       OR NEW.pi_session_id IS DISTINCT FROM OLD.pi_session_id
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.model_id IS DISTINCT FROM OLD.model_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'recording Pi session identity is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_pi_sessions_identity ON recording_pi_sessions;
CREATE TRIGGER recording_pi_sessions_identity
BEFORE UPDATE OR DELETE ON recording_pi_sessions
FOR EACH ROW EXECUTE FUNCTION enforce_recording_pi_session_identity();

CREATE TABLE IF NOT EXISTS recording_pi_events (
    event_id            TEXT        PRIMARY KEY,
    tenant              TEXT        NOT NULL,
    recording_id        TEXT        NOT NULL,
    pi_session_id       TEXT        NOT NULL,
    event_type          TEXT        NOT NULL,
    turn_index          INTEGER     NOT NULL DEFAULT 0 CHECK (turn_index >= 0),
    payload             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    usage               JSONB,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant, recording_id, pi_session_id)
        REFERENCES recording_pi_sessions (tenant, recording_id, pi_session_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_recording_pi_events_timeline
    ON recording_pi_events (tenant, recording_id, occurred_at, event_id);

CREATE OR REPLACE FUNCTION reject_recording_pi_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'recording_pi_events are immutable; append a new event instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_pi_events_immutable ON recording_pi_events;
CREATE TRIGGER recording_pi_events_immutable
BEFORE UPDATE OR DELETE ON recording_pi_events
FOR EACH ROW EXECUTE FUNCTION reject_recording_pi_event_mutation();

CREATE TABLE IF NOT EXISTS recording_artifacts (
    artifact_id         TEXT        PRIMARY KEY,
    tenant              TEXT        NOT NULL,
    recording_id        TEXT        NOT NULL,
    revision            INTEGER     NOT NULL CHECK (revision >= 0),
    kind                TEXT        NOT NULL,
    content_hash        TEXT        NOT NULL,
    storage_ref         TEXT,
    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant, recording_id)
        REFERENCES recording_sessions (tenant, recording_id) ON DELETE RESTRICT,
    UNIQUE (tenant, recording_id, revision, kind, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_recording_artifacts_revision
    ON recording_artifacts (tenant, recording_id, revision, kind);

CREATE OR REPLACE FUNCTION enforce_recording_artifact_revision()
RETURNS trigger AS $$
BEGIN
    IF NEW.revision > 0 AND NOT EXISTS (
        SELECT 1 FROM recording_revisions
        WHERE tenant = NEW.tenant
          AND recording_id = NEW.recording_id
          AND revision = NEW.revision
    ) THEN
        RAISE EXCEPTION 'recording artifact references an unknown revision';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_artifact_revision_exists ON recording_artifacts;
CREATE TRIGGER recording_artifact_revision_exists
BEFORE INSERT ON recording_artifacts
FOR EACH ROW EXECUTE FUNCTION enforce_recording_artifact_revision();

CREATE OR REPLACE FUNCTION reject_recording_artifact_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'recording_artifacts are immutable; append a new artifact instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recording_artifacts_immutable ON recording_artifacts;
CREATE TRIGGER recording_artifacts_immutable
BEFORE UPDATE OR DELETE ON recording_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_recording_artifact_mutation();
