# ADR 0005: Use shadcn-svelte date controls and formatted date answers

Status: Accepted
Date: 2026-07-03

`ask_user_question` date fields use a required model-provided `dateFormat` string that is passed to a frontend date-control wrapper to configure display and output formats such as `yyyy-MM-dd` and `yyyy-MM-dd HH:mm`. The Dano Bridge validates model-call parameters so the browser receives a renderable date-control configuration, but it does not parse dates as business values, convert user answers, normalize empty values, or reject answers that do not look like `dateFormat`. shadcn-svelte documents Date Picker as a composition of Calendar, trigger button, and optional Input primitives rather than a single component with `dateFormat` or `inputType` props, so Dano date fields use a thin frontend wrapper around those primitives.
