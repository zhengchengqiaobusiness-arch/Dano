from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from dano.execution.page.recorder import RecordSession, _RECORDER_JS
from dano.execution.page.recording_field_evidence import bind_field_evidence


@pytest.mark.asyncio
async def test_recorder_survives_document_replacement() -> None:
    recorder = RecordSession()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1600, "height": 800})
            await context.add_init_script(f"({_RECORDER_JS})()")
            await context.expose_binding("__danoRecord", recorder._on_record)
            page = await context.new_page()
            recorder._context = context
            recorder.page = page
            recorder._attach_diag_handlers(page)

            # Portal-style pages commonly open about:blank first and then replace
            # the document in the same Window. The recorder must bind to the new
            # document instead of trusting a stale Window-level installation flag.
            await page.set_content(
                '<label for="query">关键字</label>'
                '<input id="query" name="csmc">'
                '<button>查询</button>'
            )
            query_box = await page.locator("#query").bounding_box()
            assert query_box is not None
            await recorder.dispatch_input({
                "kind": "click",
                "nx": (query_box["x"] + query_box["width"] / 2) / 1600,
                "ny": (query_box["y"] + query_box["height"] / 2) / 800,
            })
            await recorder.dispatch_input({"kind": "text", "text": "123123"})

            button_box = await page.get_by_text("查询").bounding_box()
            assert button_box is not None
            await recorder.dispatch_input({
                "kind": "click",
                "nx": (button_box["x"] + button_box["width"] / 2) / 1600,
                "ny": (button_box["y"] + button_box["height"] / 2) / 800,
            })
            await page.wait_for_timeout(100)
        finally:
            await browser.close()

    actions = [event for event in recorder.steps if event.get("op") == "fill"]
    snapshots = list(recorder.form_snapshots)

    assert any(event.get("field") == "关键字" for event in actions)
    assert any(
        any("csmc" in field.get("field_aliases", []) for field in snapshot.get("fields", []))
        for snapshot in snapshots
    )


@pytest.mark.asyncio
async def test_persistent_tree_selection_is_recorded_as_field_evidence() -> None:
    recorder = RecordSession()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1600, "height": 800})
            await context.add_init_script(f"({_RECORDER_JS})()")
            await context.expose_binding("__danoRecord", recorder._on_record)
            page = await context.new_page()
            recorder._context = context
            recorder.page = page
            recorder._attach_diag_handlers(page)
            await page.set_content(
                '<section><div>部门列表</div><div><div class="app-tree" data-field="ssbmId">'
                '<div class="app-tree-node" data-key="0202">市交通运输局(27)</div>'
                '<div class="app-tree-node" data-key="0303">市农业农村局(12)</div>'
                '</div></div></section>'
            )

            item = page.get_by_text("市交通运输局(27)", exact=True)
            box = await item.bounding_box()
            assert box is not None
            await recorder.dispatch_input({
                "kind": "click",
                "nx": (box["x"] + box["width"] / 2) / 1600,
                "ny": (box["y"] + box["height"] / 2) / 800,
            })
            await page.wait_for_timeout(100)
        finally:
            await browser.close()

    selection = next(step for step in recorder.steps if step.get("op") == "pick")
    assert selection["field"] == "部门列表"
    assert "ssbmId" in selection["field_aliases"]
    assert selection["selected_label"] == "市交通运输局(27)"
    assert selection["selected_value"] == "0202"
    assert selection["options"] == [
        {"label": "市交通运输局(27)", "value": "0202"},
        {"label": "市农业农村局(12)", "value": "0303"},
    ]

    bound = bind_field_evidence(
        [{
            "request_id": "req-search",
            "method": "POST",
            "url": "https://example.test/search",
            "post_data": {"pageNum": 1, "pageSize": 10, "ssbmId": "0202"},
            "page_id": selection["page_id"],
            "frame_id": selection["frame_id"],
            "trigger_action_id": selection["action_id"],
        }],
        recorder.recorded_page_events(),
        recorder.recorded_field_evidence(),
        recorder.recorded_page_enum_options(),
    )
    department = next(item for item in bound if item.get("wire_path") == "body.ssbmId")
    assert department["binding_status"] == "bound"
    assert department["label"] == "部门列表"
