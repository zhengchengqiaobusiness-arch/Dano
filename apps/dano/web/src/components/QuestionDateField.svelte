<script lang="ts">
  import { CalendarDate, getLocalTimeZone, type DateValue } from "@internationalized/date";
  import { Calendar } from "bits-ui";
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
  let open = $state(false);
  let dateValue = $state<DateValue | undefined>();
  let timeValue = $state("00:00");
  let initialValueSynced = $state(false);
  let lastPropValue = $state<string | undefined>(undefined);
  const displayValue = $derived(formattedValue(dateValue, timeValue));

  $effect(() => {
    if (initialValueSynced && value === lastPropValue) return;
    initialValueSynced = true;
    lastPropValue = value;
    if (!value) {
      dateValue = undefined;
      timeValue = "00:00";
      return;
    }
    const parsed = parseAskUserQuestionDateValue(value, dateFormat);
    if (!parsed) return;
    dateValue = new CalendarDate(
      parsed.getFullYear(),
      parsed.getMonth() + 1,
      parsed.getDate(),
    );
    timeValue = `${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`;
  });

  function formattedValue(nextDate = dateValue, nextTime = timeValue): string | undefined {
    if (!nextDate) return undefined;
    const date = nextDate.toDate(getLocalTimeZone());
    if (includesTime) {
      const [hour = "0", minute = "0"] = nextTime.split(":");
      date.setHours(Number(hour), Number(minute), 0, 0);
    }
    return formatAskUserQuestionDateValue(date, dateFormat);
  }

  function emit(nextDate = dateValue, nextTime = timeValue) {
    const nextValue = formattedValue(nextDate, nextTime);
    onValueChange(nextValue);
  }

  function handleDateChange(nextDate: DateValue | undefined) {
    dateValue = nextDate;
    open = false;
    emit(nextDate, timeValue);
  }

  function handleTimeInput(event: Event) {
    timeValue = event.currentTarget instanceof HTMLInputElement
      ? event.currentTarget.value
      : "00:00";
    emit(dateValue, timeValue);
  }

  function clearValue() {
    dateValue = undefined;
    timeValue = "00:00";
    onValueChange(undefined);
  }
</script>

<div class="question-date-field">
  <button
    id={`${id}-trigger`}
    type="button"
    class="question-input question-date-trigger"
    disabled={disabled}
    aria-expanded={open}
    onclick={() => {
      open = !open;
    }}
  >
    <span>{displayValue ?? placeholder}</span>
    <ChevronDown size={16} aria-hidden="true" />
  </button>

  {#if open}
    <div class="question-date-popover">
      <Calendar.Root
        type="single"
        bind:value={dateValue}
        onValueChange={handleDateChange}
        fixedWeeks
        {disabled}
        class="question-calendar"
      >
        {#snippet children({ months, weekdays })}
          <Calendar.Header class="question-calendar-header">
            <Calendar.PrevButton class="question-button secondary question-calendar-nav" aria-label="Previous month">‹</Calendar.PrevButton>
            <Calendar.Heading class="question-calendar-heading" />
            <Calendar.NextButton class="question-button secondary question-calendar-nav" aria-label="Next month">›</Calendar.NextButton>
          </Calendar.Header>
          {#each months as month}
            <Calendar.Grid class="question-calendar-grid">
              <Calendar.GridHead>
                <Calendar.GridRow>
                  {#each weekdays as weekday}
                    <Calendar.HeadCell class="question-calendar-weekday">{weekday}</Calendar.HeadCell>
                  {/each}
                </Calendar.GridRow>
              </Calendar.GridHead>
              <Calendar.GridBody>
                {#each month.weeks as week}
                  <Calendar.GridRow>
                    {#each week as date}
                      <Calendar.Cell {date} month={month.value} class="question-calendar-cell">
                        <Calendar.Day class="question-calendar-day" />
                      </Calendar.Cell>
                    {/each}
                  </Calendar.GridRow>
                {/each}
              </Calendar.GridBody>
            </Calendar.Grid>
          {/each}
        {/snippet}
      </Calendar.Root>
    </div>
  {/if}

  {#if includesTime}
    <input
      class="question-input question-time-input"
      type="time"
      step="60"
      value={timeValue}
      disabled={disabled || !dateValue}
      oninput={handleTimeInput}
    />
  {/if}

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
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  :global(.question-date-trigger) {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    text-align: left;
    cursor: pointer;
  }

  :global(.question-date-popover) {
    z-index: 20;
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
    color: var(--bg);
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

  .question-time-input {
    max-width: 180px;
  }

  .question-date-clear {
    width: fit-content;
  }
</style>
