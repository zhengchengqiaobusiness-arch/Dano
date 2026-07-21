# Dano `ask_user_question` Tool Call Contract

This document records the current Dano model-facing contract for the
`ask_user_question` tool. The source contract comes from Dano's
`apps/dano/src/bridge/ask-user-question.ts`. The schema below describes the
recommended canonical authoring shape. Runtime admission is intentionally
broader: the TypeBox tool schema accepts best-effort values and the bridge
normalizes them before producing the strongly typed Card Request.

It intentionally does not include the standalone extension's additions:
`dataSource.headers`, `dataSource.cookies`, or the top-level
`dataSourceBaseUrl` parameter.

## description

```text
Ask the user for structured input during execution.

When the user asks to fill in a form, complete a form, or provide form fields, use ask_user_question to collect the fields instead of asking in assistant text. Every non-confirmation question must include a context-based recommended default so the user can usually submit directly. String defaults must be non-empty; never use default:"". required:true controls whether the user may submit an empty answer.

Use exactly one ask_user_question call per assistant response. If you need more than one answer, provide a form title and use only the questions array: {"title":"请假申请","questions":[{"id":"leave_type","question":"请假类型？","options":["事假",{"id":"sick","label":"病假"}],"default":"事假","required":true},{"id":"start_at","question":"开始时间？","inputType":"date","dateFormat":"yyyy-MM-dd HH:mm","default":"2026-07-08 09:00","required":true},{"id":"reason","question":"原因？","default":"个人事务","fieldAssist":true,"required":true}]}. When questions is present, put every field's options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside the matching questions[] item; do not include top-level confirm or top-level field configuration.

For a single question, use top-level question/options/inputType/fieldAssist/dateFormat/required/dataSource/multiple/default. For multiple questions, use title plus questions[]. fieldAssist controls generation and polishing actions for text fields; it defaults to false for single-line text and true for textarea. Dates require inputType:"date" plus dateFormat, for example "yyyy-MM-dd" or "yyyy-MM-dd HH:mm"; Dano returns the user's submitted date value as-is. required defaults to false; set required:true when an empty answer must not be submitted. default is required and string defaults must be non-empty. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. Dano normalizes unambiguous aliases and safe scalar deviations, ignores unknown or inapplicable optional fields, and rejects only inputs that cannot preserve rendering, submission, or answer mapping. When the workflow needs final confirmation for submitted grouped forms, call {"confirm":true,"formIds":["<formId>"]} with the formId values returned by those submissions. This is only for grouped-form confirmation; use a normal single-choice question to confirm an ordinary sentence or operation. If final confirmation is not needed, continue without this call.
```

## promptSnippet

```text
Ask the user one native question card; for several fields use one questions array with one submit button
```

## promptGuidelines

```json
[
  "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
  "When the user asks to fill in a form, complete a form, or provide form fields, collect the fields with ask_user_question.",
  "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
  "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
  "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
  "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
  "Use the documented canonical parameters. Dano treats model-generated arguments as best-effort input and normalizes safe aliases or coercions, but still rejects ambiguity that could change rendering, submission, or answer mapping.",
  "Give every non-confirmation question a context-based recommended non-empty default. Do not use empty string or placeholder defaults.",
  "Set required:true only when an answer is mandatory. required defaults to false.",
  "For date fields, use inputType:\"date\" and provide dateFormat such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\". The dateFormat configures the frontend date control display and submitted output.",
  "Dano returns the user's date answer as submitted; convert it yourself if a downstream interface needs another business format.",
  "Use fieldAssist to control generation and polishing actions on text fields. It defaults to false for single-line text and true for textarea; enable it when drafting or polishing business text would help, while factual short values usually omit it.",
  "When using questions, provide a concise top-level title and put each field's id, question, options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside its questions item.",
  "When one or more submitted grouped forms require final confirmation, call ask_user_question with {confirm:true,formIds:[\"<formId>\"]} using their returned formId values. Do not send confirmation text or prior answers. If confirmation is not required, continue normally.",
  "Use confirm:true only for submitted grouped forms. To confirm an ordinary sentence or operation, ask a normal single-choice question instead."
]
```

## Compatibility and validation boundary

Top-level single questions and every `questions[]` item pass through the same
compatibility normalizer. The bridge then projects only canonical values into
`AskUserQuestionCardRequest`; compatibility-only names and malformed optional
values never cross the browser protocol boundary.

| Input path | Classification | Runtime behavior |
| --- | --- | --- |
| Unknown extra parameters | Recoverable | Ignore them. |
| `key`/`name`, `label`/`prompt`/`title`, `choices`, `input_type`/`type`/`component`, `data_source`, `multi`/`multipleSelect`, `defaultValue`/`prefill`/`value`, and Field Assist aliases | Recoverable when one canonical meaning is available | Map to the canonical field. |
| Finite numbers or booleans used for textual question, title, id, option label, or text default values | Recoverable | Convert deterministically to strings. |
| Boolean-like `true`/`false`, `1`/`0`, `yes`/`no`, or `on`/`off` values | Recoverable | Convert `required`, `multiple`, `confirm`, and Field Assist values; otherwise treat an unrecognized optional value as omitted. |
| `formIds` or compatibility `formId` containing a safely parseable JSON-stringified array | Recoverable | Treat it as the equivalent native array before selecting Submitted Forms. Ordinary scalar strings remain single form IDs. |
| Option ids/labels and optional `dataSource` fields with safely coercible scalar types | Recoverable | Normalize them and discard unknown nested fields. |
| `dateFormat`, options, `multiple`, `dataSource`, or Field Assist on a control that does not use them | Recoverable | Ignore the inapplicable fields. |
| Malformed optional presentation data that does not determine the control | Recoverable | Treat it as omitted. |
| Non-object request; malformed JSON `questions`; non-object question entries | Not recoverable | Reject because no question structure can be determined. |
| Missing question text, empty `questions`, or a grouped form without a title | Not recoverable | Reject because the question cannot be presented correctly. |
| Duplicate grouped field ids, duplicate option ids, or malformed options needed by an explicit/inferred choice control | Not recoverable | Reject because answer mapping would be ambiguous. Missing grouped ids receive deterministic positional ids. |
| Choice or multiple-choice control without static options or a valid remote data source | Not recoverable | Reject because the answer semantics cannot be determined. |
| Date control without a supported `dateFormat`, or a non-empty default that does not match it | Not recoverable | Reject because the browser value cannot be rendered and returned under the requested format. |
| Missing, empty, or incompatible non-confirmation default | Not recoverable | Reject because Dano requires a usable recommended default. |
| Confirmation with no eligible Submitted Form | Not recoverable | Reject because no authoritative form identity can be selected. |

Submission remains strict after rendering: required answers must be present,
choice answers must map to exactly one option (or the explicit Other path), and
grouped answers must map by canonical field id.

### Compatibility change evidence

Collection- and object-shaped model parameters require executable evidence in
addition to the canonical schema and prose contract. For confirmation targets,
the matrix in
`apps/dano/src/bridge/__tests__/ask-user-question-confirmation-compatibility.test.ts`
and the sanitized fixtures under its `fixtures/` directory enforce canonical,
safe JSON-string, alias, malformed, partial-valid, fallback, isolation, and
canonical-projection behavior. A compatibility change is incomplete when it
updates only the schema, prompt metadata, or this document.

Tests can enforce accepted encodings, target order, stable deduplication,
ignored-reason classes, fallback decisions, Assistant Turn isolation, leakage
boundaries, and the canonical Card Request. Reviewers must still decide whether
a new coercion is unambiguous and safe to recover, whether fallback preserves
the user's intended operation, and whether expanding the accepted input space
could map answers or targets incorrectly. See the
[model argument compatibility review checklist](agents/model-argument-compatibility-review.md).

## Confirmation lifecycle

1. Call a grouped form with `title` and `questions`.
2. The user submits the form. Dano keeps that rendered form visible and locks its controls.
3. If the workflow requires final confirmation, make a later call in the same Assistant Turn with `{"confirm":true,"formIds":["<formId>"]}` using one or more returned `formId` values. If final confirmation is not required, continue without this call.
4. Dano renders a confirmation card with the selected forms' latest saved answers. The user may cancel, return to modify, or confirm.
5. Returning to modify moves the Form Interaction to `revising`; the confirmation card displays editable Form Revisions. Saving replaces the server-side snapshot and returns the same card to confirmation.
6. Confirming returns `status:"confirmed"` plus the final complete answer. Cancelling returns `status:"cancelled"` and stops the workflow.

The saved answers and Form Interaction are Dano-owned state. The model selects
Submitted Forms by their returned `formId` values and must not repeat the prior fields. A browser refresh can recover
the latest saved snapshot while the Dano server process and runtime session are
still alive. Unsaved edits and server-process restarts are outside this contract.

## schema

### Recommended parameter shape

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "optionItem": {
      "type": "object",
      "properties": {
        "id": {
          "anyOf": [
            { "type": "string", "minLength": 1 },
            { "type": "number" }
          ]
        },
        "label": { "type": "string", "minLength": 1 },
        "extra": { "type": "object", "additionalProperties": true }
      },
      "required": ["id", "label"]
    },
    "option": {
      "anyOf": [
        { "type": "string", "minLength": 1 },
        { "$ref": "#/$defs/optionItem" }
      ]
    },
    "defaultValue": {
      "description": "Required for every non-confirmation question. Provide a context-based recommended default value. String defaults must be non-empty and must not be placeholders such as \"\".",
      "anyOf": [
        { "type": "string" },
        { "type": "number" },
        { "$ref": "#/$defs/optionItem" },
        {
          "type": "array",
          "items": {
            "anyOf": [
              { "type": "string" },
              { "type": "number" },
              { "$ref": "#/$defs/optionItem" }
            ]
          }
        },
        { "type": "boolean" }
      ]
    },
    "dataSource": {
      "type": "object",
      "properties": {
        "type": { "type": "string", "const": "api" },
        "endpoint": { "type": "string", "minLength": 1 },
        "method": { "type": "string", "enum": ["GET", "POST"] },
        "params": { "type": "object", "additionalProperties": true },
        "searchParam": { "type": "string", "minLength": 1 },
        "pageParam": { "type": "string", "minLength": 1 },
        "pageSizeParam": { "type": "string", "minLength": 1 },
        "pageSize": { "type": "number", "minimum": 1 },
        "resultPath": { "type": "string", "minLength": 1 },
        "totalPath": { "type": "string", "minLength": 1 },
        "idField": { "type": "string", "minLength": 1 },
        "labelField": { "type": "string", "minLength": 1 },
        "childrenField": { "type": "string", "minLength": 1 },
        "extraFields": {
          "type": "array",
          "items": { "type": "string", "minLength": 1 }
        }
      },
      "required": ["type", "endpoint"]
    },
    "question": {
      "type": "object",
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "question": { "type": "string", "minLength": 1 },
        "options": {
          "type": "array",
          "minItems": 2,
          "items": { "$ref": "#/$defs/option" }
        },
        "inputType": {
          "type": "string",
          "enum": ["text", "textarea", "date", "radio", "checkbox", "select", "treeSelect"]
        },
        "fieldAssist": {
          "description": "Controls whether text fields show Field Assist generation and polishing actions. Single-line text defaults to false; textarea defaults to true. Enable it when drafting or polishing business text would help; factual short values usually omit it."
        },
        "dateFormat": { "type": "string", "minLength": 1 },
        "dataSource": { "$ref": "#/$defs/dataSource" },
        "multiple": { "type": "boolean" },
        "required": { "type": "boolean" },
        "default": { "$ref": "#/$defs/defaultValue" }
      },
      "required": ["id", "question", "default"]
    }
  },
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "minLength": 1,
      "description": "Single-question call: the clear, specific question to ask the user. With questions[], top-level question/title/label/prompt is treated only as optional form instruction text; each actual field question must be inside questions[]."
    },
    "title": {
      "type": "string",
      "minLength": 1,
      "description": "Required form title when questions is present; Dano derives the confirmation title as <title>确认."
    },
    "label": { "type": "string", "minLength": 1 },
    "prompt": { "type": "string", "minLength": 1 },
    "options": {
      "type": "array",
      "minItems": 2,
      "items": { "$ref": "#/$defs/option" },
      "description": "Choices for this question. Strings remain supported; objects use stable id plus label. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text, confirmation, or remote dataSource input."
    },
    "choices": {
      "type": "array",
      "minItems": 2,
      "items": { "$ref": "#/$defs/option" }
    },
    "inputType": {
      "type": "string",
      "enum": [
        "text",
        "textarea",
        "date",
        "radio",
        "checkbox",
        "select",
        "treeSelect",
        "confirm"
      ]
    },
    "type": { "type": "string", "minLength": 1 },
    "input_type": { "type": "string", "minLength": 1 },
    "component": { "type": "string", "minLength": 1 },
    "fieldAssist": {
      "description": "Controls whether text fields show Field Assist generation and polishing actions. Single-line text defaults to false; textarea defaults to true. Enable it when drafting or polishing business text would help; factual short values usually omit it."
    },
    "dateFormat": {
      "type": "string",
      "minLength": 1,
      "description": "Required when inputType is \"date\". A frontend date-control format such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\"."
    },
    "dataSource": { "$ref": "#/$defs/dataSource" },
    "data_source": { "$ref": "#/$defs/dataSource" },
    "multiple": {
      "type": "boolean",
      "default": false,
      "description": "Set true with options to allow multiple selections."
    },
    "multi": { "type": "boolean" },
    "multipleSelect": { "type": "boolean" },
    "required": {
      "type": "boolean",
      "description": "Set true to require a non-empty answer. Defaults to false."
    },
    "default": { "$ref": "#/$defs/defaultValue" },
    "defaultValue": { "$ref": "#/$defs/defaultValue" },
    "prefill": { "$ref": "#/$defs/defaultValue" },
    "value": { "$ref": "#/$defs/defaultValue" },
    "confirm": {
      "type": "boolean",
      "const": true,
      "description": "Confirm one or more previously submitted grouped forms. Use {confirm:true,formIds:[\"<formId>\"]}; Dano supplies each selected form's title and latest submitted answers."
    },
    "formIds": {
      "type": "array",
      "minItems": 1,
      "items": {},
      "description": "Standard grouped-form confirmation target: an array of formId strings returned by earlier grouped form submissions in this Assistant Turn."
    },
    "questions": {
      "description": "Preferred for collecting more than one answer. Provide a top-level title and make exactly one ask_user_question call. Put fieldAssist inside the matching questions[] item when overriding its text-field default. A single question object is also accepted and normalized to an array. Do not include top-level confirm or top-level field configuration with questions.",
      "anyOf": [
        { "$ref": "#/$defs/question" },
        {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/question" }
        }
      ]
    }
  },
  "allOf": [
    {
      "if": { "required": ["questions"] },
      "then": { "required": ["title"] }
    }
  ]
}
```

### Result schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "answer": {
      "description": "Canonical answer value returned to the model: string or number id, id array, text string, or boolean value.",
      "anyOf": [
        { "type": "string" },
        { "type": "number" },
        {
          "type": "array",
          "items": {
            "anyOf": [{ "type": "string" }, { "type": "number" }]
          }
        },
        { "type": "boolean" }
      ]
    }
  },
  "anyOf": [
    {
      "type": "object",
      "properties": {
        "status": { "type": "string", "const": "answered" },
        "formId": { "type": "string" },
        "answer": {
          "anyOf": [
            { "$ref": "#/$defs/answer" },
            {
              "type": "object",
              "additionalProperties": { "$ref": "#/$defs/answer" }
            }
          ]
        }
      },
      "required": ["status", "answer"]
    },
    {
      "type": "object",
      "properties": {
        "status": { "type": "string", "const": "confirmed" },
        "answer": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/answer" }
        },
        "confirmationOfToolCallId": { "type": "string" },
        "forms": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "formId": { "type": "string" },
              "answer": {
                "type": "object",
                "additionalProperties": { "$ref": "#/$defs/answer" }
              }
            },
            "required": ["formId", "answer"]
          }
        }
      },
      "required": ["status", "answer", "confirmationOfToolCallId", "forms"]
    },
    {
      "type": "object",
      "properties": {
        "status": { "type": "string", "const": "cancelled" }
      },
      "required": ["status"]
    }
  ]
}
```
