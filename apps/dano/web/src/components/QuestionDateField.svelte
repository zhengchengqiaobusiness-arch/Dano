<script lang="ts">
  import { onMount } from "svelte";
  import { CalendarDate, Time, getLocalTimeZone, type DateValue } from "@internationalized/date";
  import { DatePicker } from "bits-ui";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import { formatAskUserQuestionDateValue, isAskUserQuestionDateTimeFormat, parseAskUserQuestionDateValue } from "@dano/types/ask-user-question-date";
  import { t } from "../i18n";
  import { formatNativeDateInputValue, parseNativeDateInputValue } from "../utils/questionDateNative";
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
  const MOBILE_PICKER_QUERY = "(max-width: 640px)";
  const hourOptions = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, "0"));
  const minuteOptions = Array.from({ length: 60 }, (_, minute) => String(minute).padStart(2, "0"));
  const timeSelectControls = [
    { part: "hour", labelKey: "questionTool.hour", options: hourOptions },
    { part: "minute", labelKey: "questionTool.minute", options: minuteOptions },
  ] as const;
  let open = $state(false);
  let useNativePicker = $state(false);
  let dateValue = $state<DateValue | undefined>();
  let timeValue = $state(new Time(0, 0));
  let initialValueSynced = $state(false);
  let lastPropValue = $state<string | undefined>(undefined);
  const displayValue = $derived(formattedValue(dateValue, timeValue));
  const nativeInputValue = $derived(formatNativeDateInputValue(
    dateValue
      ? {
          year: dateValue.year,
          month: dateValue.month,
          day: dateValue.day,
          hour: timeValue.hour,
          minute: timeValue.minute,
        }
      : undefined,
    includesTime,
  ));

  onMount(() => {
    const query = window.matchMedia(MOBILE_PICKER_QUERY);
    const syncPicker = () => {
      useNativePicker = query.matches;
      if (useNativePicker) open = false;
    };
    syncPicker();
    query.addEventListener("change", syncPicker);
    return () => query.removeEventListener("change", syncPicker);
  });

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

  function updateTime(hour: number, minute: number) {
    const nextTime = new Time(hour, minute);
    timeValue = nextTime;
    emit(dateValue, nextTime);
  }

  function handleTimePartChange(part: "hour" | "minute", event: Event) {
    if (!(event.currentTarget instanceof HTMLSelectElement)) return;
    const value = Number(event.currentTarget.value);
    updateTime(
      part === "hour" ? value : timeValue.hour,
      part === "minute" ? value : timeValue.minute,
    );
  }

  function handleNativeInput(event: Event) {
    if (!(event.currentTarget instanceof HTMLInputElement)) return;
    if (!event.currentTarget.value) {
      clearValue();
      return;
    }
    const parts = parseNativeDateInputValue(event.currentTarget.value, includesTime);
    if (!parts) return;
    const nextDate = new CalendarDate(parts.year, parts.month, parts.day);
    const nextTime = includesTime
      ? new Time(parts.hour, parts.minute)
      : timeValue;
    dateValue = nextDate;
    timeValue = nextTime;
    emit(nextDate, nextTime);
  }

  function clearValue() {
    dateValue = undefined;
    timeValue = new Time(0, 0);
    onValueChange(undefined);
  }

</script>

<div class="question-date-field">
  {#if useNativePicker}
    <div class="question-date-native-control" class:disabled>
      <input
        id={`${id}-trigger`}
        class="question-input question-date-native"
        type={includesTime ? "datetime-local" : "date"}
        value={nativeInputValue}
        step={includesTime ? 60 : undefined}
        {placeholder}
        aria-placeholder={placeholder || undefined}
        {disabled}
        {required}
        oninput={handleNativeInput}
      />
      {#if !nativeInputValue && placeholder}
        <span class="question-date-native-placeholder" aria-hidden="true">
          {placeholder}
        </span>
      {/if}
      <span class="question-date-native-icon" aria-hidden="true">
        <ChevronDown size={16} />
      </span>
    </div>
  {:else}
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
              <div class="question-time-selects">
                {#each timeSelectControls as control}
                  <div class="question-time-select-control">
                    <select
                      class="question-input question-time-select"
                      aria-label={t(control.labelKey)}
                      value={String(control.part === "hour" ? timeValue.hour : timeValue.minute).padStart(2, "0")}
                      disabled={disabled || !dateValue}
                      onchange={(event) => handleTimePartChange(control.part, event)}
                    >
                      {#each control.options as option}
                        <option value={option}>{option}</option>
                      {/each}
                    </select>
                    <ChevronDown size={15} aria-hidden="true" />
                  </div>
                {/each}
              </div>
            </div>
          {/if}
        </DatePicker.Content>
      </div>
    </DatePicker.Root>
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

  .question-time-selects {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .question-time-select-control {
    position: relative;
    min-width: 0;
  }

  .question-time-select-control > :global(svg) {
    position: absolute;
    top: 50%;
    right: 10px;
    color: var(--text-subtle);
    pointer-events: none;
    transform: translateY(-50%);
  }

  .question-time-select {
    appearance: none;
    width: 100%;
    padding-right: 32px;
    background: var(--control-bg);
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .question-time-select:focus-visible,
  .question-date-native:focus-visible {
    border-color: var(--accent);
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .question-date-native-control {
    position: relative;
    width: 100%;
  }

  .question-date-native {
    width: 100%;
    padding-right: 40px;
  }

  .question-date-native::-webkit-calendar-picker-indicator {
    position: absolute;
    top: 0;
    right: 0;
    width: 40px;
    height: 100%;
    margin: 0;
    padding: 0;
    opacity: 0;
    cursor: pointer;
  }

  .question-date-native-placeholder {
    position: absolute;
    inset: 1px 40px 1px 1px;
    display: flex;
    align-items: center;
    overflow: hidden;
    padding-left: 11px;
    border-radius: 9px 0 0 9px;
    background: var(--control-bg);
    color: var(--text-subtle);
    pointer-events: none;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .question-date-native:focus + .question-date-native-placeholder {
    opacity: 0;
  }

  .question-date-native-control.disabled .question-date-native-placeholder {
    opacity: 0.5;
  }

  .question-date-native-icon {
    position: absolute;
    top: 50%;
    right: 12px;
    display: grid;
    place-items: center;
    color: var(--text-subtle);
    pointer-events: none;
    transform: translateY(-50%);
  }

  .question-date-native-control.disabled .question-date-native-icon {
    opacity: 0.5;
  }

  @media (max-width: 640px) {
    .question-date-control-row {
      grid-template-columns: minmax(0, 1fr);
    }

    :global(.question-date-trigger),
    :global(.question-date-input) {
      width: 100%;
    }
  }
</style>
