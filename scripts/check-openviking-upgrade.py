"""Exercise an old -> new OpenViking data volume with synthetic owners only.

Run inside the disposable server container:
  python check-openviking-upgrade.py seed|read|verify /etc/openviking/ov.conf /probe/state.json
Seed on the previous image, stop it, then verify on the candidate image using
the same disposable volume. The private state contains synthetic user keys.
No production accounts or data are read. Output contains acceptance flags only.
"""

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    action, config_path, state_path = sys.argv[1:]
    assert action in ("seed", "read", "verify")
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    base = "http://127.0.0.1:%d/api/v1" % config["server"]["port"]
    root_key = config["server"]["root_api_key"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(method, path, key=None, body=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-API-Key"] = key
        request = urllib.request.Request(
            base + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode("utf-8"),
        )
        try:
            with opener.open(request, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, None

    def result(method, path, key, body=None):
        status, payload = call(method, path, key, body)
        assert status == 200, "UPGRADE_PROBE_HTTP_%d" % status
        assert payload["status"] == "ok"
        return payload["result"]

    state_file = Path(state_path)
    if action == "seed":
        assert not state_file.exists(), "UPGRADE_PROBE_STATE_EXISTS"
        account = "dano-upgrade-" + secrets.token_hex(8)
        result("POST", "/admin/accounts", root_key,
               {"account_id": account, "admin_user_id": "admin"})
        keys = {user: result("POST", "/admin/accounts/%s/users" % account,
                            root_key, {"user_id": user, "role": "user"})["user_key"]
                for user in ("alice", "bob")}
        uri = "viking://user/alice/memories/upgrade-probe.md"
        content = "升级验收的合成偏好：旅行方案使用蓝色表格，并列出每项预算。"
        result("POST", "/content/write", keys["alice"],
               {"uri": uri, "content": content, "mode": "replace", "wait": True})
        session = result("POST", "/sessions", keys["alice"],
                         {"auto_commit_policy": None})
        sid = session["session_id"]
        result("POST", "/sessions/%s/messages" % sid, keys["alice"],
               {"role": "user", "content": content,
                "source_message_ids": ["dano-upgrade-source-1"]})
        state = {"account": account, "keys": keys, "uri": uri,
                 "content": content, "sessionId": sid}
        fd = os.open(str(state_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output)
        print(json.dumps({"oldImageSeeded": True}), flush=True)
        return

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["account"].startswith("dano-upgrade-")
    alice, bob = state["keys"]["alice"], state["keys"]["bob"]
    query = urllib.parse.urlencode({"uri": state["uri"]})
    old_content = result("GET", "/content/read?" + query, alice)
    assert state["content"] in json.dumps(old_content, ensure_ascii=False)
    assert call("GET", "/content/read?" + query)[0] in (401, 403)
    assert call("GET", "/content/read?" + query, bob)[0] == 403
    assert call("POST", "/content/write", bob,
                {"uri": state["uri"], "content": "foreign", "mode": "replace"})[0] == 403
    assert call("DELETE", "/fs?" + query, bob)[0] == 403
    assert state["content"] in json.dumps(
        result("GET", "/content/read?" + query, alice), ensure_ascii=False)
    sid = state["sessionId"]
    context = result("GET", "/sessions/%s/context" % sid, alice)
    assert "dano-upgrade-source-1" in json.dumps(context)
    found = result("POST", "/search/find", alice,
                   {"query": "旅行方案蓝色表格预算", "target_uri": state["uri"], "limit": 5})
    assert "upgrade-probe.md" in json.dumps(found), "OLD_MEMORY_INDEX_UNAVAILABLE"
    print(json.dumps({"oldContentAndSourcePreserved": True,
                      "oldMemorySearchable": True, "crossUserDenied": True,
                      "unauthenticatedDenied": True}), flush=True)
    if action == "read":
        return
    result("POST", "/sessions/%s/messages" % sid, alice,
           {"role": "user", "content": "长期偏好：我的发布报告始终先写验收结果，再写变更明细。",
            "source_message_ids": ["dano-upgrade-new-source-2"]})
    task = result("POST", "/sessions/%s/commit" % sid, alice,
                  {"keep_recent_count": 0})
    task_id = task["task_id"]
    for _ in range(90):
        task = result("GET", "/tasks/%s" % task_id, alice)
        if task["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(2)
    assert task["status"] == "completed", "NEW_EXTRACTION_NOT_COMPLETED"
    archive = task["result"]
    assert archive["session_id"] == sid
    assert archive["archive_uri"].startswith("viking://user/alice/sessions/%s/history/" % sid)
    assert archive["memory_diff_uri"] == archive["archive_uri"] + "/memory_diff.json"
    diff = result("GET", "/content/read?" + urllib.parse.urlencode(
        {"uri": archive["memory_diff_uri"]}), alice)
    assert diff, "NEW_EXTRACTION_DIFF_UNAVAILABLE"
    recalled = result("POST", "/search/find", alice,
                      {"query": "发布报告的长期格式偏好", "target_uri": "viking://user/alice/memories",
                       "limit": 10})
    assert "验收结果" in json.dumps(recalled, ensure_ascii=False), "NEW_EXTRACTION_NOT_RECALLED"
    print(json.dumps({"newCommitCompleted": True, "archiveAndMemoryDiffCompatible": True,
                      "newMemoryRecalled": True}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never emit raw HTTP bodies, private config, state or credentials.
        code = str(error) if isinstance(error, AssertionError) else type(error).__name__
        allowed = ("UPGRADE_PROBE_", "OLD_MEMORY_", "NEW_EXTRACTION_")
        print(json.dumps({"error": code if code.startswith(allowed) else type(error).__name__}), flush=True)
        sys.exit(1)
