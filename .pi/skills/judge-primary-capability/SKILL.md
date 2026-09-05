---
name: judge-primary-capability
description: Mark each recorded capability as primary, lookup, or noise from evidence. Use when analyzing a session, deciding what review/export includes, or when business_skill_judge_primary / business_skill_analyze runs.
---

# Judge primary capability

You decide `role`. Do not invent capabilities. Every id in the input must appear in the output.

## Roles

- `primary` — the user-facing business action on this page: search/list/ask, create, update, review, delete, upload, download.
- `lookup` — exists only to fill another field (dict, user/dept page, stock/balance, product simple-list).
- `noise` — login, captcha, IM, unread, tenant bootstrap, permission ping, process-definition polling.

## How to decide

1. `authenticate` or login/captcha/IM/unread paths → `noise`.
2. A write (`create`/`update`/`review`/`delete`/`upload`) with a real submit/save/approve click → `primary`.
3. `download`/`export` with an export click → `primary`.
4. A query that returns a page/list/ask result the user asked for on this page → `primary`.
5. A query used only as `from:` or select candidates for a write/query field → `lookup`.
6. POST that is not a user search and not a submit — companion widgets such as hot-words, getAppId, save_chat — → `lookup` or `noise`, never a write `primary`.
7. Detail GET that only reloads after save → `lookup` when a same-page write exists; `primary` only if this session is a standalone detail/query page.
8. `/user/page`, `/dept/list`, dict-data → `lookup` unless the user recorded that page as the business goal.

## Operation

Correct `operation` from method + button text + response shape:

- GET/HEAD → `query`
- DELETE → `delete`
- User clicked 搜索/查询/检索, or the body is a paged list, or an ask field (`sys_query`/`question`) → `query` even if POST
- User clicked 新增/保存/提交 on a create form → `create`
- User clicked 修改/编辑 → `update`
- User clicked 审核/通过/驳回 → `review`
- Otherwise do not force `action` onto a search companion

## Output

For each capability: `id`, `operation`, `role`, `title`, `description` (Chinese, short). Title is the business name, not the raw path.