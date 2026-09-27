"""Probe frozen correction/deletion sources against OpenViking public APIs.

Run inside a disposable OpenViking container with synthetic credentials and
the frozen issue477-evaluation.json. This checks upstream write/read/delete
primitives only. It does not count as Dano, Browser, model-answer, or
scenario-specific frozen-matrix acceptance.
"""

import argparse
import json
import secrets
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 3:
        raise SystemExit("repetitions must be 1..3")
    config = json.loads(args.config.read_text())
    frozen = json.loads(args.cases.read_text())["cases"]
    correction = [case for case in frozen if case["category"] == "correction"]
    deletion = [case for case in frozen if case["category"] == "deletion"]
    recall = {case["id"]: case for case in frozen if case["category"] == "recall"}
    if len(correction) != 10 or len(deletion) != 10:
        raise SystemExit("frozen correction/deletion case set required")
    if any(case["sourceCase"] not in recall or case["owner"] != recall[case["sourceCase"]]["owner"]
           for case in correction + deletion):
        raise SystemExit("frozen source mapping mismatch")
    owners = sorted({case["owner"] for case in correction + deletion})
    if len(owners) != 5:
        raise SystemExit("five frozen owners required")

    base = "http://127.0.0.1:1933/api/v1"
    root_key = config["server"]["root_api_key"]
    records = []
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        def call(method, path, key, *, body=None, params=None, expected=(200,)):
            response = client.request(method, path, headers={"X-API-Key": key},
                                      json=body, params=params)
            if response.status_code not in expected:
                raise RuntimeError(f"{method}_{path.split('/')[1]}_{response.status_code}")
            return response

        for repetition in range(1, args.repetitions + 1):
            account = "eval477p_" + secrets.token_hex(5)
            call("POST", "/admin/accounts", root_key,
                 body={"account_id": account, "admin_user_id": "admin"})
            keys = {}
            for owner in owners:
                response = call("POST", f"/admin/accounts/{account}/users", root_key,
                                body={"user_id": owner, "role": "user"})
                keys[owner] = response.json()["result"]["user_key"]
            for case in correction + deletion:
                owner = case["owner"]
                source = recall[case["sourceCase"]]
                key = keys[owner]
                uri = f"viking://user/{owner}/memories/{case['id'].lower()}.md"
                original = f"# Synthetic {case['id']}\n{source['fact']}\n"
                started = time.monotonic()
                call("POST", "/content/write", key,
                     body={"uri": uri, "content": original, "mode": "create",
                           "wait": True, "timeout": 120})
                before = call("GET", "/content/read", key,
                              params={"uri": uri, "raw": True}).json()["result"]
                # OpenViking may append its own MEMORY_FIELDS trailer to a
                # raw public read; the authored content must remain exact.
                if not before.startswith(original):
                    raise RuntimeError("INITIAL_READBACK_MISMATCH")
                if case["category"] == "correction":
                    updated = f"# Synthetic {case['id']}\n{source['fact'].replace(case['old'], case['replacement'])}\n"
                    if updated == original or case["expected"] not in updated:
                        raise RuntimeError("FROZEN_REPLACEMENT_MISMATCH")
                    call("POST", "/content/write", key,
                         body={"uri": uri, "content": updated, "mode": "replace",
                               "wait": True, "timeout": 120})
                    after = call("GET", "/content/read", key,
                                 params={"uri": uri, "raw": True}).json()["result"]
                    passed = after.startswith(updated) and not after.startswith(original)
                else:
                    call("DELETE", "/fs", key,
                         params={"uri": uri, "recursive": "false", "wait": "true"})
                    absent = call("GET", "/content/read", key,
                                  params={"uri": uri, "raw": "true"}, expected=(404,))
                    search = call("POST", "/search/find", key,
                                  body={"query": source["question"],
                                        "target_uri": f"viking://user/{owner}/memories",
                                        "limit": 10}).json()["result"]
                    passed = absent.status_code == 404 and all(
                        item["uri"] != uri for item in search.get("memories", []))
                records.append({"caseId": case["id"], "repetition": repetition,
                                "primitive": case["category"], "passed": passed,
                                "elapsedMs": round((time.monotonic() - started) * 1000)})
                if not passed:
                    raise RuntimeError("PUBLIC_API_READBACK_FAILED")
    summary = {"suite": "issue477-public-api-correction-delete-primitives",
               "scope": "upstream OpenViking only; not frozen Dano/Browser/model acceptance",
               "server": "real OpenViking v0.4.20",
               "attempts": len(records), "passed": sum(record["passed"] for record in records),
               "repetitions": args.repetitions}
    print(json.dumps({"summary": summary, "records": records}))


if __name__ == "__main__":
    main()
