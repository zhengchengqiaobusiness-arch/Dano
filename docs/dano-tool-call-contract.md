# Dano `ask_user_question` Tool Call Contract

This document records the current Dano model-facing contract for the
`ask_user_question` tool. The source contract comes from Dano's
`apps/dano/src/bridge/ask-user-question.ts`. Its TypeBox expressions are fully
expanded below as standalone JSON Schema, so the schema does not depend on an
unexplained imported `Type` builder.

It intentionally does not include the standalone extension's additions:
`dataSource.headers`, `dataSource.cookies`, or the top-level
`dataSourceBaseUrl` parameter.

## description

```text
Ask the user for structured input during execution.

When the user asks to fill in a form, complete a form, or provide form fields, use ask_user_question to collect the fields instead of asking in assistant text. Every non-confirmation question must include a context-based recommended default so the user can usually submit directly. String defaults must be non-empty; never use default:"". required:true controls whether the user may submit an empty answer.

Use exactly one ask_user_question call per assistant response. If you need more than one answer, provide a form title and use only the questions array: {"title":"请假申请","questions":[{"id":"leave_type","question":"请假类型？","options":["事假",{"id":"sick","label":"病假"}],"default":"事假","required":true},{"id":"start_at","question":"开始时间？","inputType":"date","dateFormat":"yyyy-MM-dd HH:mm","default":"2026-07-08 09:00","required":true},{"id":"reason","question":"原因？","default":"个人事务","required":true}]}. When questions is present, put every field's options, inputType, dateFormat, required, dataSource, multiple, and default inside the matching questions[] item; do not include top-level confirm or top-level field configuration.

For a single question, use top-level question/options/inputType/dateFormat/required/dataSource/multiple/default. For multiple questions, use title plus questions[]. Dates require inputType:"date" plus dateFormat, for example "yyyy-MM-dd" or "yyyy-MM-dd HH:mm"; Dano returns the user's submitted date value as-is. required defaults to false; set required:true when an empty answer must not be submitted. default is required and string defaults must be non-empty. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. After a grouped form is answered, call exactly {"confirm":true}; Dano binds the latest saved form and returns its final full answer only after the user confirms.
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
  "Give every non-confirmation question a context-based recommended non-empty default. Do not use empty string or placeholder defaults.",
  "Set required:true only when an answer is mandatory. required defaults to false.",
  "For date fields, use inputType:\"date\" and provide dateFormat such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\". The dateFormat configures the frontend date control display and submitted output.",
  "Dano returns the user's date answer as submitted; convert it yourself if a downstream interface needs another business format.",
  "When using questions, provide a concise top-level title and put each field's id, question, options, inputType, dateFormat, required, dataSource, multiple, and default inside its questions item.",
  "After a grouped form is answered, call ask_user_question with only {confirm:true}. Do not send confirmation text, the prior answers, or a relation id; Dano binds the latest saved form."
]
```

## Confirmation lifecycle

1. Call a grouped form with `title` and `questions`.
2. The user submits the form. Dano keeps that rendered form visible and locks its controls.
3. Call `{"confirm":true}` in the same tool execution workflow. Any other confirmation field is invalid.
4. Dano renders `<title>确认` with the latest saved answers. The user may cancel, return to modify, or confirm.
5. Returning to modify unlocks the original form. Saving replaces the server-side snapshot and returns to the confirmation card.
6. Confirming returns `status:"confirmed"` plus the final complete answer. Cancelling returns `status:"cancelled"` and stops the workflow.

The confirmation relation and saved answer are Dano-owned state. The model must
not send a relation id or repeat the prior fields. A browser refresh can recover
the latest saved snapshot while the Dano server process and runtime session are
still alive. Unsaved edits and server-process restarts are outside this contract.

## schema

### Parameter schema

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
      "description": "Call with only {confirm:true} after the user submitted a grouped form. Dano supplies the form title and latest saved answers."
    },
    "questions": {
      "description": "Preferred for collecting more than one answer. Provide a top-level title and make exactly one ask_user_question call. A single question object is also accepted and normalized to an array. Do not include top-level confirm or top-level field configuration with questions.",
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
        "confirmationOfToolCallId": { "type": "string" }
      },
      "required": ["status", "answer", "confirmationOfToolCallId"]
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
