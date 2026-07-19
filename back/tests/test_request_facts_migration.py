from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "015_request_facts_canonical.sql"


def test_request_facts_migration_backs_up_before_mutating_assets():
    sql = MIGRATION.read_text(encoding="utf-8")

    create_backup = sql.index("CREATE TABLE IF NOT EXISTS request_facts_migration_backup")
    backup_assets = sql.index("INSERT INTO request_facts_migration_backup (source_table, source_id, original_body)")
    update_assets = sql.index("UPDATE assets")
    backup_drafts = sql.index(
        "INSERT INTO request_facts_migration_backup (source_table, source_id, original_body)",
        backup_assets + 1,
    )
    update_drafts = sql.index("UPDATE asset_drafts")

    assert create_backup < backup_assets < update_assets
    assert backup_drafts < update_drafts
    assert "original_body  JSONB       NOT NULL" in sql


def test_request_facts_migration_covers_known_release_snapshot_locations():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "{api_request,_release_snapshot,flow_spec,meta,request_graph}" in sql
    assert "{_release_snapshot,flow_spec,meta,request_graph}" in sql
    assert "{flow_spec,meta,request_graph}" in sql
    assert "migrate_recording_flow_spec_to_request_facts" in sql
    assert "COALESCE(spec -> 'meta', '{}'::jsonb) - 'request_graph'" in sql
    assert "DROP FUNCTION migrate_recording_flow_spec_to_request_facts(JSONB)" in sql