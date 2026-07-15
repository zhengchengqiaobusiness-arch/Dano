<script lang="ts">
  import { CalendarDate, Time, getLocalTimeZone, type DateValue } from "@internationalized/date";
  import { DatePicker, TimeField } from "bits-ui";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import { formatAskUserQuestionDateValue, isAskUserQuestionDateTimeFormat, parseAskUserQuestionDateValue } from "@dano/types/ask-user-question-date";
  import { t } from "../i18n";
  import "./questionToolControls.css";

  let {
    id,
    value = undefined as string | undefined,
    dateFormat,
    disabled = false,
    required = false,
    placeholder = "",
    onValueChange,
  }: {
    id: string;
    value?: string;
    dateFormat: string;
    disabled?: boolean;
    required?: boolean;
    placeholder?: string;
    onValueChange: (value: string | undefined) => void;
  } = $props();

  const includesTime = $derived(isAskUserQuestionDateTimeFormat(dateFormat));
  const POPOVER_GAP_PX = 8;
  let open = $state(false);
  let dateValue = $state<DateValue | undefined>();
  let timeValue = $state(new Time(0, 0));
  let initialValueSynced = $state(false);
  let lastPropValue = $state<string | undefined>(undefined);
  const displayValue = $derived(formattedValue(dateValue, timeValue));

  $effect(() => {
    if (initialValueSynced && value === lastPropValue) return;
    initialValueSynced = true;
    lastPropValue = value;
    if (!value) {
      dateValue = undefined;
      timeValue = new Time(0, 0);
      return;
    }
    const parsed = parseAskUserQuestionDateValue(value, dateFormat);
    if (!parsed) return;
    dateValue = new CalendarDate(
      parsed.getFullYear(),
      parsed.getMonth() + 1,
      parsed.getDate(),
    );
    timeValue = new Time(parsed.getHours(), parsed.getMinutes());
  });

  function formattedValue(nextDate = dateValue, nextTime = timeValue): string | undefined {
    if (!nextDate) return undefined;
    const date = nextDate.toDate(getLocalTimeZone());
    if (includesTime) {
      date.setHours(nextTime.hour, nextTime.minute, 0, 0);
    }
    return formatAskUserQuestionDateValue(date, dateFormat);
  }

  function emit(nextDate = dateValue, nextTime = timeValue) {
    const nextValue = formattedValue(nextDate, nextTime);
    onValueChange(nextValue);
  }

  function handleDateChange(nextDate: DateValue | undefined) {
    dateValue = nextDate;
    if (!includesTime) open = false;
    emit(nextDate, timeValue);
  }

  function handleTimeChange(nextTime: Time | undefined) {
    if (!nextTime) return;
    timeValue = nextTime;
    emit(dateValue, nextTime);
  }

  function clearValue() {
    dateValue = undefined;
    timeValue = new Time(0, 0);
    onValueChange(undefined);
  }

</script>

<div class="question-date-field">
  <DatePicker.Root
    bind:open
    bind:value={dateValue}
    onValueChange={handleDateChange}
    closeOnDateSelect={false}
    fixedWeeks
    preventDeselect
    {disabled}
  >
    <div class="question-date-control-row">
      <DatePicker.Input class="question-date-input">
        {#snippet children()}
          <DatePicker.Trigger
            id={`${id}-trigger`}
            class="question-input question-date-trigger"
            {disabled}
          >
            <span>{displayValue ?? placeholder}</span>
            <ChevronDown size={16} aria-hidden="true" />
          </DatePicker.Trigger>
        {/snippet}
      </DatePicker.Input>

      <DatePicker.Content
        class="question-date-popover"
        align="start"
        sideOffset={POPOVER_GAP_PX}
        collisionPadding={POPOVER_GAP_PX}
        trapFocus={false}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <DatePicker.Calendar class="question-calendar">
          {#snippet children({ months, weekdays })}
            <DatePicker.Header class="question-calendar-header">
              <DatePicker.PrevButton class="question-button secondary question-calendar-nav" aria-label="Previous month">‹</DatePicker.PrevButton>
              <DatePicker.Heading class="question-calendar-heading" />
              <DatePicker.NextButton class="question-button secondary question-calendar-nav" aria-label="Next month">›</DatePicker.NextButton>
            </DatePicker.Header>
            {#each months as month}
              <DatePicker.Grid class="question-calendar-grid">
                <DatePicker.GridHead>
                  <DatePicker.GridRow>
                    {#each weekdays as weekday}
                      <DatePicker.HeadCell class="question-calendar-weekday">{weekday}</DatePicker.HeadCell>
                    {/each}
                  </DatePicker.GridRow>
                </DatePicker.GridHead>
                <DatePicker.GridBody>
                  {#each month.weeks as week}
                    <DatePicker.GridRow>
                      {#each week as date}
                        <DatePicker.Cell {date} month={month.value} class="question-calendar-cell">
                          <DatePicker.Day class="question-calendar-day" />
                        </DatePicker.Cell>
                      {/each}
                    </DatePicker.GridRow>
                  {/each}
                </DatePicker.GridBody>
              </DatePicker.Grid>
            {/each}
          {/snippet}
        </DatePicker.Calendar>

        {#if includesTime}
          <div class="question-date-time-section">
            <TimeField.Root
              bind:value={timeValue}
              onValueChange={handleTimeChange}
              granularity="minute"
              hourCycle={24}
              disabled={disabled || !dateValue}
            >
              <TimeField.Input class="question-time-input">
                {#snippet children({ segments })}
                  {#each segments as segment}
                    <TimeField.Segment
                      part={segment.part}
                      class="question-time-segment"
                    >
                      {segment.value}
                    </TimeField.Segment>
                  {/each}
                {/snippet}
              </TimeField.Input>
            </TimeField.Root>
          </div>
        {/if}
      </DatePicker.Content>
    </div>
  </DatePicker.Root>

  {#if !required && dateValue}
    <button
      type="button"
      class="question-button secondary question-date-clear"
      disabled={disabled}
      onclick={clearValue}
    >
      {t("questionTool.clearDate")}
    </button>
  {/if}
</div>

<style>
  .question-date-field {
    --question-date-picker-width: 260px;

    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .question-date-control-row {
    display: grid;
    grid-template-columns: max-content;
    gap: 8px;
  }

  :global(.question-date-input) {
    width: var(--question-date-picker-width);
  }

  :global(.question-date-trigger) {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    text-align: left;
    cursor: pointer;
    width: 100%;
  }

  :global(.question-date-popover) {
    z-index: 20;
    width: var(--question-date-picker-width);
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--panel);
    color: var(--text);
    box-shadow: var(--shadow-raised);
  }

  :global(.question-calendar) {
    display: grid;
    gap: 8px;
  }

  :global(.question-calendar-header) {
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr) 32px;
    align-items: center;
    gap: 8px;
  }

  :global(.question-calendar-heading) {
    color: var(--text);
    font-size: 0.9rem;
    font-weight: 700;
    text-align: center;
  }

  :global(.question-calendar-nav) {
    width: 32px;
    height: 32px;
    padding: 0;
  }

  :global(.question-calendar-grid) {
    border-collapse: collapse;
  }

  :global(.question-calendar-weekday) {
    width: 34px;
    height: 28px;
    color: var(--text-subtle);
    font-size: 0.74rem;
    font-weight: 600;
    text-align: center;
  }

  :global(.question-calendar-cell) {
    padding: 2px;
  }

  :global(.question-calendar-day) {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 1px solid transparent;
    border-radius: 8px;
    color: var(--text);
    cursor: pointer;
  }

  :global(.question-calendar-day:hover:not([data-disabled]):not([data-unavailable]):not([data-selected])) {
    background: color-mix(in srgb, var(--accent) 10%, var(--bg));
    border-color: color-mix(in srgb, var(--accent) 28%, var(--border));
  }

  :global(.question-calendar-day[data-today]:not([data-selected])) {
    border-color: var(--accent);
    color: var(--accent);
  }

  :global(.question-calendar-day[data-outside-month]),
  :global(.question-calendar-day[data-outside-visible-months]) {
    color: var(--text-subtle);
    opacity: 0.46;
  }

  :global(.question-calendar-day[data-selected]) {
    background: var(--accent);
    color: var(--on-accent);
    font-weight: 700;
  }

  :global(.question-calendar-day[data-disabled]),
  :global(.question-calendar-day[data-unavailable]) {
    cursor: not-allowed;
    opacity: 0.35;
    text-decoration: line-through;
  }

  :global(.question-calendar-day[data-focused]),
  :global(.question-calendar-day:focus-visible) {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .question-date-time-section {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }

  :global(.question-time-input) {
    display: flex;
    align-items: center;
    width: 100%;
    min-height: 44px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--control-bg);
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  :global(.question-time-input:focus-within) {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--focus-ring);
  }

  :global(.question-time-segment) {
    min-width: 2ch;
    padding: 4px 5px;
    border-radius: 6px;
    color: var(--text);
    text-align: center;
    outline: none;
  }

  :global(.question-time-segment[data-segment="literal"]) {
    min-width: auto;
    padding-inline: 1px;
  }

  :global(.question-time-segment:focus) {
    background: var(--accent);
    color: var(--on-accent);
  }

  :global(.question-time-segment[data-disabled]) {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .question-date-clear {
    width: fit-content;
  }

  @media (max-width: 640px) {
    .question-date-control-row {
      grid-template-columns: minmax(0, 1fr);
    }

    :global(.question-date-trigger),
    :global(.question-date-input),
    :global(.question-time-input) {
      width: 100%;
    }

    :global(.question-date-popover) {
      width: min(100%, 320px);
    }

  }
</style>
