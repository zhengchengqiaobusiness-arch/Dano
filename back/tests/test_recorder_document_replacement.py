from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from dano.execution.page.recorder import RecordSession, _RECORDER_JS


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
