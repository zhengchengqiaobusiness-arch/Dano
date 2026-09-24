"""Run frozen #477 cross-USER cases against the real OpenViking public API.

Run inside the isolated Dano app container with its private config and a copy
of issue477-evaluation.json. The script creates only synthetic USERs, emits no
keys or raw USER IDs, and removes exactly those USERs before returning. This
proves the upstream USER boundary; Dano and Browser boundaries need separate
acceptance evidence.
"""

import argparse
import hashlib
import json
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def request(base, method, path, key, *, body=None, params=None, forged_user=None):
    headers = {"X-API-Key": key}
    if forged_user:
        headers.update({"X-OpenViking-Account": forged_user[0],
                        "X-OpenViking-User": forged_user[1]})
    return httpx.request(method, base + path, headers=headers, json=body,
                         params=params, timeout=120, trust_env=False)


def result(base, method, path, key, *, body=None, params=None):
    response = request(base, method, path, key, body=body, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"SETUP_{method}_{path.split('/')[1]}_{response.status_code}")
    return response.json()["result"]


def user_ids(base, account, key):
    listing = result(base, "GET", f"/admin/accounts/{account}/users", key)
    users = listing["users"] if isinstance(listing, dict) else listing
    return {entry["user_id"] for entry in users}


def source_content(response):
    if response.status_code != 200:
        return ""
    return json.dumps(response.json().get("result"), ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 3:
        raise SystemExit("repetitions must be 1..3")
    config = json.loads(args.config.read_text())
    fixture = json.loads(args.cases.read_text())
    account, admin_key = config["accountId"], config["managementKey"]
    origin = config["baseUrl"].rstrip("/")
    if not origin.startswith("http://openviking:"):
        raise SystemExit("isolated internal OpenViking origin required")
    base = origin + "/api/v1"
    frozen = fixture["cases"]
    recall = {case["id"]: case for case in frozen if case["category"] == "recall"}
    cases = [case for case in frozen if case["category"] == "isolation"]
    if len(cases) != 20 or len({case["id"] for case in cases}) != 20:
        raise SystemExit("frozen 20-case isolation set required")
    names = sorted({case[field] for case in cases for field in ("actor", "targetOwner")})
    required_sources = {case["targetSourceCase"] for case in cases}
    if len(names) != 5 or not required_sources.issubset(recall):
        raise SystemExit("frozen owner/source mapping required")

    baseline = user_ids(base, account, admin_key)
    records, repetitions = [], []
    for repetition in range(1, args.repetitions + 1):
        nonce = secrets.token_hex(5)
        users = {name: f"eval477m_{nonce}_{name}" for name in names}
        keys, created, documents, sessions = {}, [], {}, {}
        problem = None
        try:
            for name in names:
                response = result(base, "POST", f"/admin/accounts/{account}/users", admin_key,
                                  body={"user_id": users[name], "role": "user"})
                created.append(users[name])
                keys[name] = response["user_key"]
            for source_id in sorted(required_sources):
                source = recall[source_id]
                name = source["owner"]
                uri = f"viking://user/{users[name]}/memories/{source_id.lower()}.md"
                content = f"# {source_id}\n{source['fact']}\n"
                result(base, "POST", "/content/write", keys[name],
                       body={"uri": uri, "content": content, "mode": "create",
                             "wait": True, "timeout": 120})
                own = request(base, "GET", "/content/read", keys[name],
                              params={"uri": uri, "raw": True})
                if own.status_code != 200 or source["fact"] not in source_content(own):
                    raise RuntimeError("SOURCE_READBACK_FAILED")
                documents[source_id] = {"uri": uri, "fact": source["fact"], "owner": name}
            for name in sorted({case["targetOwner"] for case in cases}):
                session = result(base, "POST", "/sessions", keys[name],
                                 body={"auto_commit_policy": None})
                sid = session["session_id"]
                result(base, "POST", f"/sessions/{sid}/messages", keys[name],
                       body={"role": "user", "content": f"Synthetic {name} source",
                             "source_message_ids": [f"eval477m_{nonce}_{name}"]})
                sessions[name] = sid

            def probe(case):
                actor, target = case["actor"], case["targetOwner"]
                doc = documents[case["targetSourceCase"]]
                forged = (account, users[target])
                start = time.monotonic()
                operation = case["operation"]
                if operation == "search":
                    response = request(base, "POST", "/search/find", keys[actor],
                                       body={"query": recall[case["targetSourceCase"]]["question"],
                                             "target_uri": f"viking://user/{users[target]}/memories",
                                             "limit": 10}, forged_user=forged)
                elif operation == "directRead":
                    response = request(base, "GET", "/content/read", keys[actor],
                                       params={"uri": doc["uri"], "raw": True}, forged_user=forged)
                elif operation == "directWrite":
                    response = request(base, "POST", "/content/write", keys[actor],
                                       body={"uri": doc["uri"], "content": "SYNTHETIC_UNAUTHORIZED_REPLACE",
                                             "mode": "replace", "wait": True, "timeout": 120},
                                       forged_user=forged)
                elif operation == "export":
                    response = request(base, "POST", "/pack/export", keys[actor],
                                       body={"uri": doc["uri"]}, forged_user=forged)
                elif operation == "replay":
                    response = request(base, "POST", f"/sessions/{sessions[target]}/messages",
                                       keys[actor], body={"role": "user",
                                                          "content": "SYNTHETIC_UNAUTHORIZED_REPLAY",
                                                          "source_message_ids": [case["targetSourceCase"]]},
                                       forged_user=forged)
                else:
                    raise RuntimeError("UNKNOWN_FROZEN_OPERATION")
                leaked = doc["fact"] in response.text
                target_changed = False
                actor_isolated = False
                if operation == "directWrite":
                    own = request(base, "GET", "/content/read", keys[target],
                                  params={"uri": doc["uri"], "raw": True})
                    target_changed = own.status_code != 200 or doc["fact"] not in source_content(own)
                if operation == "replay":
                    own = request(base, "GET", f"/sessions/{sessions[target]}", keys[target])
                    target_changed = own.status_code != 200 or own.json()["result"]["message_count"] != 1
                    if response.status_code == 200:
                        actor_session = request(base, "GET", f"/sessions/{sessions[target]}", keys[actor])
                        if actor_session.status_code == 200:
                            actor_result = actor_session.json()["result"]
                            target_result = own.json()["result"] if own.status_code == 200 else {}
                            actor_isolated = (actor_result.get("created_by_user_id") == users[actor]
                                              and actor_result.get("uri") != target_result.get("uri")
                                              and actor_result.get("message_count") == 1)
                denied = response.status_code in (401, 403, 404)
                if operation == "search" and response.status_code == 200:
                    denied = not leaked and doc["uri"] not in response.text
                if operation == "replay" and response.status_code == 200:
                    denied = actor_isolated
                passed = denied and not leaked and not target_changed
                return {"caseId": case["id"], "repetition": repetition,
                        "operation": operation, "status": response.status_code,
                        "elapsedMs": round((time.monotonic() - start) * 1000),
                        "leakedTargetFact": leaked, "mutatedTarget": target_changed,
                        "isolatedActorSession": actor_isolated if operation == "replay" else None,
                        "passed": passed}

            with ThreadPoolExecutor(max_workers=5) as pool:
                records.extend(pool.map(probe, cases))
        except Exception as error:
            problem = type(error).__name__
        finally:
            statuses = []
            for uid in created:
                try:
                    response = request(base, "DELETE", f"/admin/accounts/{account}/users/{uid}", admin_key)
                    statuses.append(response.status_code)
                except Exception:
                    statuses.append(0)
            try:
                restored = user_ids(base, account, admin_key) == baseline
            except Exception:
                restored = False
            repetitions.append({"repetition": repetition,
                                "createdSyntheticUsers": len(created),
                                "cleanupStatuses": statuses,
                                "baselineUsersRestored": restored,
                                "setupErrorType": problem})
        if problem or not repetitions[-1]["baselineUsersRestored"]:
            break
    summary = {"suite": "issue477-frozen-isolation", "server": "real OpenViking",
               "requestedRepetitions": args.repetitions, "completedAttempts": len(records),
               "passedAttempts": sum(item["passed"] for item in records),
               "allHardCasesPassed": len(records) == 20 * args.repetitions
                   and all(item["passed"] for item in records),
               "allSyntheticUsersCleaned": all(item["baselineUsersRestored"] for item in repetitions),
               "runFingerprint": hashlib.sha256(str(repetitions).encode()).hexdigest()[:12]}
    print(json.dumps({"summary": summary, "repetitions": repetitions,
                      "records": records}, ensure_ascii=False))
    if not summary["allHardCasesPassed"] or not summary["allSyntheticUsersCleaned"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
