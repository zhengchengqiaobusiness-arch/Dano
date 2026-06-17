"""真实 OA 测试服务器(独立进程,真实 HTTP + Token 鉴权 + 有状态业务)。

用途:在没有真实企业 OA 测试环境时,用它当**真实接入目标**做真实测试——
Dano 网关经真实 HTTP 打到这里:真实换 token、真实试跑动作、真实写回比对字段。
这是真实服务器,不是进程内 Fake;生产时把 base_url 换成贵公司真实 OA 测试环境即可。

启动:  python -m examples.real_oa_server          (监听 http://localhost:9001)
鉴权:  POST /auth/token  {"apikey":"test-key-123"} -> {"token":"..."}
        之后请求需带 Authorization: Bearer <token>

业务端点(与 examples/onboarding/oa.json 的 OpenAPI 一一对应):
  GET  /oa/balance            query_balance   查询年假余额(L1)
  POST /oa/leave              create_leave    提交请假(L3,扣减余额、生成单号)
  GET  /oa/approval           query_approval  查询审批状态(L1)
字段写回探针(接入期流程2 字段映射写回实测,测试环境专用):
  POST /oa/_field_probe       写入 {field,value} 到测试草稿
  GET  /oa/_field_probe       读回某字段值,供 Dano 写入→读回→比对
"""

from __future__ import annotations

from datetime import datetime

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Real OA Test Server", version="1.0.0")

_VALID_APIKEY = "test-key-123"
_ISSUED_TOKEN = "oa-real-token-abc"

# 有状态业务:年假余额 + 已提交请假单 + 字段写回探针暂存
_state: dict = {
    "balance": 10,                     # 年假余额(天)
    "requests": {},                    # request_id -> {status, days, applicant}
    "seq": 0,
    "probe": {},                       # field -> value(写回比对暂存,测试环境专用)
}


class TokenReq(BaseModel):
    apikey: str


@app.post("/auth/token")
async def issue_token(req: TokenReq) -> dict:
    """用 apikey 换 token(真实鉴权握手)。"""
    if req.apikey != _VALID_APIKEY:
        raise HTTPException(status_code=401, detail="invalid apikey")
    return {"token": _ISSUED_TOKEN, "expires_in": 3600}


def _auth(authorization: str | None) -> None:
    if authorization != f"Bearer {_ISSUED_TOKEN}":
        raise HTTPException(status_code=401, detail="missing/invalid token")


# ───────────────────────── 业务端点 ─────────────────────────
@app.get("/oa/balance")
async def query_balance(
    applicant: str | None = None, authorization: str | None = Header(default=None)
) -> dict:
    """查询年假余额(L1 查询动作)。试跑时无参,返回服务账号默认余额。"""
    _auth(authorization)
    return {"applicant": applicant or "service", "balance": _state["balance"], "unit": "day"}


@app.post("/oa/leave")
async def create_leave(
    body: dict | None = None, authorization: str | None = Header(default=None)
) -> dict:
    """提交请假(L3 写动作)。扣减余额、落单、返回单号+状态。

    试跑时 body 可为空(days=0,不扣余额),真实调用按入参扣减——
    接入期沙箱试跑因此不污染余额,运行期真实调用才扣减。
    """
    _auth(authorization)
    data = body or {}
    days = int(data.get("days", 0) or 0)
    _state["seq"] += 1
    request_id = f"REQ-{datetime.now():%Y%m%d}-{_state['seq']:04d}"
    if days > 0:
        _state["balance"] -= days
    _state["requests"][request_id] = {
        "status": "待审批",
        "days": days,
        "applicant": data.get("applicant", "service"),
    }
    return {"request_id": request_id, "status": "待审批", "remaining_balance": _state["balance"]}


@app.get("/oa/approval")
async def query_approval(
    request_id: str | None = None, authorization: str | None = Header(default=None)
) -> dict:
    """查询审批状态(L1 查询动作)。试跑时无 request_id,返回最近一单或空态。"""
    _auth(authorization)
    if request_id and request_id in _state["requests"]:
        rec = _state["requests"][request_id]
        return {"request_id": request_id, **rec}
    if not request_id and _state["requests"]:
        rid = next(reversed(_state["requests"]))
        return {"request_id": rid, **_state["requests"][rid]}
    return {"request_id": request_id, "status": "未找到"}


# ───────────────── 字段写回探针(测试环境专用,接入期流程2)─────────────────
class FieldProbe(BaseModel):
    field: str
    value: str


@app.post("/oa/_field_probe")
async def field_probe_write(req: FieldProbe, authorization: str | None = Header(default=None)) -> dict:
    """写入一个字段值到测试草稿(写回实测的「写入」段)。"""
    _auth(authorization)
    _state["probe"][req.field] = req.value
    return {"field": req.field, "value": req.value, "stored": True}


@app.get("/oa/_field_probe")
async def field_probe_read(field: str, authorization: str | None = Header(default=None)) -> dict:
    """读回一个字段值(写回实测的「读回」段),供 Dano 比对一致性。"""
    _auth(authorization)
    return {"field": field, "value": _state["probe"].get(field)}


if __name__ == "__main__":
    print("Real OA test server on http://localhost:9001  (apikey=test-key-123)")
    uvicorn.run(app, host="127.0.0.1", port=9001, log_level="warning")
