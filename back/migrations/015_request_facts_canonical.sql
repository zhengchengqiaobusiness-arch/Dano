-- Make RequestFacts the canonical request ledger for persisted recording assets.
-- Every affected JSON body is backed up before it is changed. The migration is
-- transactional through run_migrations(), and the backup table is retained for
-- audit/rollback.

CREATE TABLE IF NOT EXISTS request_facts_migration_backup (
    source_table   TEXT        NOT NULL,
    source_id      UUID        NOT NULL,
    original_body  JSONB       NOT NULL,
    backed_up_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_table, source_id)
);

CREATE OR REPLACE FUNCTION migrate_recording_flow_spec_to_request_facts(spec JSONB)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    graph JSONB := COALESCE(spec #> '{meta,request_graph}', '{}'::jsonb);
    facts JSONB := COALESCE(spec -> 'request_facts', '{}'::jsonb);
    requests JSONB := '[]'::jsonb;
    analyses JSONB := '{}'::jsonb;
    usages JSONB := '{}'::jsonb;
    item JSONB;
    rid TEXT;
    bucket TEXT;
BEGIN
    IF graph = '{}'::jsonb THEN
        RETURN spec;
    END IF;

    -- Graph-only assets predate RequestFacts. Convert them once from the
    -- immutable all_requests ledger; existing RequestFacts are never rebuilt.
    IF jsonb_typeof(facts -> 'requests') IS DISTINCT FROM 'array'
       OR jsonb_array_length(COALESCE(facts -> 'requests', '[]'::jsonb)) = 0 THEN
        FOR item IN
            SELECT value
            FROM jsonb_array_elements(COALESCE(graph -> 'all_requests', '[]'::jsonb))
        LOOP
            rid := COALESCE(
                NULLIF(item ->> 'request_id', ''),
                CASE
                    WHEN item ? 'request_index' THEN 'idx:' || (item ->> 'request_index')
                    ELSE 'sig:' || substr(md5(item::text), 1, 12)
                END
            );
            item := jsonb_set(item, '{request_id}', to_jsonb(rid), true);
            requests := requests || jsonb_build_array(
                item
                - 'role' - 'semantic_roles' - 'keep' - 'reason' - 'confidence'
                - 'evidence' - 'bucket' - 'filter_reason' - 'state'
                - 'materialized_step_id' - 'used_by_capabilities'
            );

            bucket := CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(graph -> 'selected_steps', '[]'::jsonb)) selected
                    WHERE NULLIF(selected ->> 'request_id', '') = rid
                       OR (
                           selected ? 'request_index'
                           AND item ? 'request_index'
                           AND selected ->> 'request_index' = item ->> 'request_index'
                       )
                ) THEN 'selected_steps'
                WHEN EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(graph -> 'candidate_reads', '[]'::jsonb)) candidate
                    WHERE NULLIF(candidate ->> 'request_id', '') = rid
                       OR (
                           candidate ? 'request_index'
                           AND item ? 'request_index'
                           AND candidate ->> 'request_index' = item ->> 'request_index'
                       )
                ) THEN 'candidate_reads'
                ELSE 'filtered_requests'
            END;

            analyses := analyses || jsonb_build_object(
                rid,
                jsonb_build_object(
                    'request_id', rid,
                    'role', COALESCE(item ->> 'role', ''),
                    'semantic_roles', COALESCE(item -> 'semantic_roles', '[]'::jsonb),
                    'keep', CASE lower(COALESCE(item ->> 'keep', '')) WHEN 'true' THEN true ELSE false END,
                    'reason', COALESCE(item ->> 'reason', ''),
                    'confidence', CASE
                        WHEN COALESCE(item ->> 'confidence', '') ~ '^-?[0-9]+([.][0-9]+)?$'
                            THEN (item ->> 'confidence')::double precision
                        ELSE 0
                    END,
                    'evidence', COALESCE(item -> 'evidence', '{}'::jsonb),
                    'bucket', bucket,
                    'filter_reason', COALESCE(item ->> 'filter_reason', '')
                )
            );
            usages := usages || jsonb_build_object(
                rid,
                jsonb_build_object(
                    'request_id', rid,
                    'materialized_step_id', COALESCE(item ->> 'materialized_step_id', ''),
                    'state', CASE
                        WHEN NULLIF(item ->> 'materialized_step_id', '') IS NOT NULL
                            THEN 'materialized'
                        ELSE COALESCE(NULLIF(item ->> 'state', ''), 'captured')
                    END,
                    'used_by_capabilities', COALESCE(item -> 'used_by_capabilities', '[]'::jsonb),
                    'capability_memberships', '[]'::jsonb
                )
            );
        END LOOP;

        facts := jsonb_build_object(
            'protocol', 'dano.request_facts.v1',
            'requests', requests,
            'diagnostics', COALESCE(spec -> 'diagnostics', '[]'::jsonb),
            'page_events', COALESCE(spec #> '{meta,page_events}', '[]'::jsonb),
            'option_sources', '[]'::jsonb,
            'analysis', analyses,
            'usage', usages
        );
        spec := jsonb_set(spec, '{request_facts}', facts, true);
    END IF;

    spec := jsonb_set(
        spec,
        '{meta}',
        COALESCE(spec -> 'meta', '{}'::jsonb) - 'request_graph',
        true
    );
    RETURN spec;
END;
$$;

INSERT INTO request_facts_migration_backup (source_table, source_id, original_body)
SELECT 'assets', asset_id, body
FROM assets
WHERE body #> '{api_request,_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{flow_spec,meta,request_graph}' IS NOT NULL
ON CONFLICT (source_table, source_id) DO NOTHING;

INSERT INTO request_facts_migration_backup (source_table, source_id, original_body)
SELECT 'asset_drafts', asset_draft_id, body
FROM asset_drafts
WHERE body #> '{api_request,_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{flow_spec,meta,request_graph}' IS NOT NULL
ON CONFLICT (source_table, source_id) DO NOTHING;

UPDATE assets
SET body = CASE
    WHEN body #> '{api_request,_release_snapshot,flow_spec}' IS NOT NULL THEN
        jsonb_set(
            body,
            '{api_request,_release_snapshot,flow_spec}',
            migrate_recording_flow_spec_to_request_facts(
                body #> '{api_request,_release_snapshot,flow_spec}'
            ),
            false
        )
    WHEN body #> '{_release_snapshot,flow_spec}' IS NOT NULL THEN
        jsonb_set(
            body,
            '{_release_snapshot,flow_spec}',
            migrate_recording_flow_spec_to_request_facts(body #> '{_release_snapshot,flow_spec}'),
            false
        )
    WHEN body -> 'flow_spec' IS NOT NULL THEN
        jsonb_set(
            body,
            '{flow_spec}',
            migrate_recording_flow_spec_to_request_facts(body -> 'flow_spec'),
            false
        )
    ELSE body
END
WHERE body #> '{api_request,_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{flow_spec,meta,request_graph}' IS NOT NULL;

UPDATE asset_drafts
SET body = CASE
    WHEN body #> '{api_request,_release_snapshot,flow_spec}' IS NOT NULL THEN
        jsonb_set(
            body,
            '{api_request,_release_snapshot,flow_spec}',
            migrate_recording_flow_spec_to_request_facts(
                body #> '{api_request,_release_snapshot,flow_spec}'
            ),
            false
        )
    WHEN body #> '{_release_snapshot,flow_spec}' IS NOT NULL THEN
        jsonb_set(
            body,
            '{_release_snapshot,flow_spec}',
            migrate_recording_flow_spec_to_request_facts(body #> '{_release_snapshot,flow_spec}'),
            false
        )
    WHEN body -> 'flow_spec' IS NOT NULL THEN
        jsonb_set(
            body,
            '{flow_spec}',
            migrate_recording_flow_spec_to_request_facts(body -> 'flow_spec'),
            false
        )
    ELSE body
END
WHERE body #> '{api_request,_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{_release_snapshot,flow_spec,meta,request_graph}' IS NOT NULL
   OR body #> '{flow_spec,meta,request_graph}' IS NOT NULL;

DROP FUNCTION migrate_recording_flow_spec_to_request_facts(JSONB);