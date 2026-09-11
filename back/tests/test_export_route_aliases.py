from dano.gateway.app import app


def _route_rows() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or []:
            rows.add((method, path))
    return rows


def test_frontend_export_paths_are_registered_on_gateway():
    rows = _route_rows()
    assert ("POST", "/v1/recording-results/{result_id}/export-skill") in rows
    assert ("POST", "/v1/pi-recordings/{result_id}/export-skill") in rows
    assert ("POST", "/v1/skills/export") in rows
    assert ("GET", "/v1/export/directory") in rows
    assert ("PUT", "/v1/export/directory") in rows
