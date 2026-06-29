"""可观测回查(流程9 的 UI 版):提交后回到验证视图,确认数据**真的变了** —— 不读接口。

页面直驱模式不解析提交触发的那十几个接口;"到底成没成"由本引擎在 UI 层断言:
回到列表/详情,**带我提交值的记录是否真出现 + 计数是否按预期增长**。这是 API 版 `fact_check` 的 UI 等价物,
落地"查查数据是否改变"。副作用常异步,轮询若干次再判,避免"查太早"假阴性。

二态铁律:通过 = 计数 +≥1 且(声明了 expect_contains 时)出现一条含全部提交值的新记录;否则**不谎报**。
dry 模式不调用本引擎(留 `partially_verified`,不声称已生效)。绝不用坐标,只语义定位。
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class ReadbackView(BaseModel):
    """回查视图:提交后去哪看结果、看什么。绝不用坐标。"""

    verify_url: str = Field(default="", description="验证视图 URL(列表/详情);空=留在当前页")
    row_locator: str = Field(default="", description="记录行的语义定位(css=.el-table__row / role=row 等)")
    expect_contains: list[str] = Field(
        default_factory=list, description="新记录须包含的提交值(语义子串匹配,如请假类型/日期)")


async def snapshot_observable(driver, view: ReadbackView) -> dict:  # noqa: ANN001
    """抓可观测签名:导航到验证视图(给定 verify_url 则)→ 记录行文本集合 + 行数。"""
    if view.verify_url:
        try:
            await driver.open(view.verify_url)
        except Exception:  # noqa: BLE001
            pass
    rows = await _row_texts(driver, view.row_locator)
    return {"count": len(rows), "rows": rows}


async def verify_readback(driver, *, before: dict, view: ReadbackView,  # noqa: ANN001
                          retries: int = 5, backoff_s: float = 1.5) -> tuple[bool, dict]:
    """二态:提交后回查数据真的变了。

    通过 = 计数 +≥1 且(有 expect_contains 时)出现一条含全部提交值的新记录。轮询兜异步。
    返回 (是否确认生效, 证据)。绝不抛(回查本身出错 → 判未通过,诚实不谎报)。
    """
    before_count = int((before or {}).get("count", 0))
    before_rows = list((before or {}).get("rows", []))
    last: dict = {}
    ok = False
    for attempt in range(max(1, retries)):
        try:
            after = await snapshot_observable(driver, view)
        except Exception as e:  # noqa: BLE001
            last = {"before_count": before_count, "after_count": before_count,
                    "grew": False, "matched": False, "attempts": attempt + 1, "error": str(e)}
            after = None
        if after is not None:
            new_rows = [r for r in after["rows"] if r not in before_rows]
            grew = after["count"] >= before_count + 1
            if view.expect_contains:
                cand = new_rows or after["rows"]
                matched = any(all(tok in r for tok in view.expect_contains) for r in cand)
                ok = grew and matched
            else:
                matched = grew
                ok = grew
            last = {"before_count": before_count, "after_count": after["count"],
                    "grew": grew, "matched": matched, "attempts": attempt + 1,
                    "new_rows": new_rows[:5]}
        if ok:
            break
        if attempt < retries - 1:
            await asyncio.sleep(backoff_s)
    log.info("readback.verify", passed=ok, before=before_count,
             after=last.get("after_count"), grew=last.get("grew"), matched=last.get("matched"))
    return ok, {"passed": ok, **last}


async def _row_texts(driver, locator: str) -> list[str]:  # noqa: ANN001
    """读验证视图记录行文本(driver.query_texts);无该能力 / 无定位 → []。"""
    fn = getattr(driver, "query_texts", None)
    if fn is None or not locator:
        return []
    try:
        return [str(t).strip() for t in (await fn(locator)) if str(t).strip()]
    except Exception:  # noqa: BLE001
        return []
