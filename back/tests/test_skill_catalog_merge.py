from dano.gateway.app import _merge_skill_manifests


def test_merge_keeps_one_row_per_recording_id():
    pg = [{"name": "oa.old", "recording_id": "rec_1", "created_at": "2020-01-01T00:00:00Z"}]
    pi = [{
        "name": "oa.rec_1",
        "skill_id": "oa.rec_1",
        "recording_id": "rec_1",
        "title": "转正办理",
        "created_at": "2026-09-10T00:00:00Z",
    }]
    merged = _merge_skill_manifests(pg, pi)
    assert [item["name"] for item in merged] == ["oa.rec_1"]
    assert merged[0]["title"] == "转正办理"
    assert merged[0]["recording_id"] == "rec_1"


def test_merge_empty_pi_keeps_pg():
    pg = [{"name": "oa.api", "created_at": "2026-01-01T00:00:00Z"}]
    assert [item["name"] for item in _merge_skill_manifests(pg, [])] == ["oa.api"]
