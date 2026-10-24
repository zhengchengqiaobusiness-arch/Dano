<script lang="ts">
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import EllipsisVertical from "@lucide/svelte/icons/ellipsis-vertical";
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button, buttonVariants } from "$lib/components/ui/button";
  import * as Dialog from "$lib/components/ui/dialog";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu";
  import * as Field from "$lib/components/ui/field";
  import { Input } from "$lib/components/ui/input";
  import * as Select from "$lib/components/ui/select";
  import { Skeleton } from "$lib/components/ui/skeleton";
  import * as Tabs from "$lib/components/ui/tabs";
  import { Textarea } from "$lib/components/ui/textarea";
  import { t } from "../i18n";

  type Repeat = "none" | "hourly" | "daily" | "weekly" | "interval";
  type RepeatPreset = "none" | "hourly" | "daily" | "weekly" | "custom";
  type IntervalUnit = "seconds" | "minutes" | "hours" | "days" | "weeks";
  type Destination = "conversation" | "standalone";
  type Tab = "tasks" | "runs";

  const HISTORY_DAYS = 30;
  const NAMED_INTERVAL_MS = {
    hourly: 3600e3,
    daily: 86400e3,
    weekly: 604800e3,
  } as const;
  const UNIT_MS: Record<IntervalUnit, number> = {
    seconds: 1000,
    minutes: 60_000,
    hours: 3600e3,
    days: 86400e3,
    weeks: 604800e3,
  };
  const TASK_GRID =
    "grid grid-cols-[minmax(0,2fr)_minmax(8rem,1fr)_minmax(7rem,1fr)_7.5rem_auto] gap-3";

  type ScheduledTask = {
    taskId: string;
    taskName: string;
    enabled: boolean;
    nextRunAt: string;
    lastRunAt?: string;
    repeat: Repeat;
    intervalMs?: number;
    destination: Destination;
    sessionPath?: string;
    payload: { userInput: string };
  };

  type ScheduledRun = {
    runId: string;
    taskId: string;
    taskName: string;
    trigger?: "schedule" | "manual";
    status?: "ok" | "error" | "skipped";
    outputSessionPath?: string;
    startedAt: string;
  };

  let {
    open = false,
    tasksUrl = null,
    sessionPath = null,
    sessionId = null,
    themeStyle = "",
    onClose = () => {},
    onOpenSession = (_path: string) => {},
  }: {
    open?: boolean;
    tasksUrl?: string | null;
    sessionPath?: string | null;
    sessionId?: string | null;
    themeStyle?: string;
    onClose?: () => void;
    onOpenSession?: (sessionPath: string) => void | Promise<void>;
  } = $props();

  let tasks = $state<ScheduledTask[]>([]);
  let runs = $state<ScheduledRun[]>([]);
  let loading = $state(false);
  let error = $state(false);
  let tab = $state<Tab>("tasks");
  let createOpen = $state(false);
  let saving = $state(false);
  let taskName = $state("");
  let userInput = $state("");
  let nextRunLocal = $state("");
  let repeatPreset = $state<RepeatPreset>("none");
  let intervalCount = $state(10);
  let intervalUnit = $state<IntervalUnit>("minutes");
  let destination = $state<Destination>("conversation");
  let generation = 0;
  let controller: AbortController | undefined;

  const conversationDisabled = $derived(!sessionPath);
  const recentRuns = $derived.by(() => {
    const cutoff = Date.now() - HISTORY_DAYS * 86400e3;
    return runs.filter((run) => Date.parse(run.startedAt) >= cutoff);
  });
  const historyCounts = $derived(countsByDay(recentRuns, HISTORY_DAYS));
  const historyHasRuns = $derived(recentRuns.length > 0);

  $effect(() => {
    if (conversationDisabled) destination = "standalone";
  });

  $effect(() => {
    if (!open) {
      controller?.abort();
      createOpen = false;
      tab = "tasks";
      return;
    }
    if (!tasksUrl) return;
    void loadTasks(tasksUrl);
    return () => controller?.abort();
  });

  $effect(() => {
    if ((open || createOpen) && !nextRunLocal) nextRunLocal = defaultNextRunLocal();
  });

  async function loadTasks(url: string) {
    const current = ++generation;
    controller?.abort();
    const next = new AbortController();
    controller = next;
    loading = true;
    error = false;
    try {
      const response = await fetch(url, { signal: next.signal, cache: "no-store" });
      if (!response.ok) throw new Error();
      const body = await response.json() as { tasks?: ScheduledTask[] };
      if (generation !== current || next.signal.aborted) return;
      tasks = Array.isArray(body.tasks) ? body.tasks : [];
      runs = await loadRuns(url, tasks, next.signal);
    } catch {
      if (generation === current && !next.signal.aborted) error = true;
    } finally {
      if (generation === current) loading = false;
    }
  }

  async function loadRuns(
    url: string,
    taskList: ScheduledTask[],
    signal: AbortSignal,
  ): Promise<ScheduledRun[]> {
    const groups = await Promise.all(
      taskList.map(async (task) => {
        try {
          const response = await fetch(`${url}/${encodeURIComponent(task.taskId)}/runs`, {
            signal,
            cache: "no-store",
          });
          if (!response.ok) return [];
          const body = await response.json() as { runs?: ScheduledRun[] };
          return (Array.isArray(body.runs) ? body.runs : []).map((run) => ({
            ...run,
            taskId: task.taskId,
            taskName: task.taskName,
          }));
        } catch {
          return [];
        }
      }),
    );
    return groups.flat().sort((left, right) => Date.parse(right.startedAt) - Date.parse(left.startedAt));
  }

  async function request(path: string, init?: RequestInit) {
    if (!tasksUrl) return;
    error = false;
    const response = await fetch(path, { cache: "no-store", ...init });
    if (!response.ok) throw new Error();
    return response.json();
  }

  function intervalMsFromForm(): number | undefined {
    if (repeatPreset === "none") return undefined;
    if (repeatPreset === "hourly" || repeatPreset === "daily" || repeatPreset === "weekly") {
      return NAMED_INTERVAL_MS[repeatPreset];
    }
    const count = Number(intervalCount);
    if (!Number.isFinite(count) || count <= 0) return undefined;
    return Math.round(count * UNIT_MS[intervalUnit]);
  }

  function formatRepeat(task: ScheduledTask): string {
    const ms =
      typeof task.intervalMs === "number" && task.intervalMs > 0
        ? task.intervalMs
        : task.repeat === "hourly" || task.repeat === "daily" || task.repeat === "weekly"
          ? NAMED_INTERVAL_MS[task.repeat]
          : undefined;
    if (!ms) return t("scheduledTasks.repeat.none");
    if (ms === NAMED_INTERVAL_MS.hourly) return t("scheduledTasks.repeat.hourly");
    if (ms === NAMED_INTERVAL_MS.daily) return t("scheduledTasks.repeat.daily");
    if (ms === NAMED_INTERVAL_MS.weekly) return t("scheduledTasks.repeat.weekly");
    const units: IntervalUnit[] = ["weeks", "days", "hours", "minutes", "seconds"];
    for (const unit of units) {
      if (ms % UNIT_MS[unit] === 0) {
        return t("scheduledTasks.every", {
          count: ms / UNIT_MS[unit],
          unit: t(`scheduledTasks.unit.${unit}`),
        });
      }
    }
    return t("scheduledTasks.every", {
      count: ms,
      unit: t("scheduledTasks.unit.milliseconds"),
    });
  }

  function formatClock(iso: string): string {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) return "";
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function formatSchedule(task: ScheduledTask): string {
    const time = formatClock(task.nextRunAt);
    if (task.repeat === "daily") return t("scheduledTasks.schedule.dailyAt", { time });
    if (task.repeat === "weekly") return t("scheduledTasks.schedule.weeklyAt", { time });
    if (task.repeat === "none") return t("scheduledTasks.schedule.onceAt", { time });
    return formatRepeat(task);
  }

  function formatNextRun(iso: string): string {
    const timestamp = Date.parse(iso);
    if (!Number.isFinite(timestamp)) return iso;
    const delta = timestamp - Date.now();
    if (delta <= 0) return t("scheduledTasks.nextRun.due");
    const minutes = Math.round(delta / 60_000);
    if (minutes < 60) return t("scheduledTasks.nextRun.inMinutes", { count: Math.max(1, minutes) });
    const hours = Math.round(delta / 3600e3);
    if (hours < 48) return t("scheduledTasks.nextRun.inHours", { count: hours });
    const days = Math.round(delta / 86400e3);
    if (days < 14) return t("scheduledTasks.nextRun.inDays", { count: days });
    return new Date(timestamp).toLocaleString();
  }

  function formatDateTime(iso: string): string {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) return iso;
    return date.toLocaleString();
  }

  function countsByDay(items: ScheduledRun[], days: number): number[] {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - (days - 1));
    const startMs = start.getTime();
    const buckets = Array.from({ length: days }, () => 0);
    for (const item of items) {
      const index = Math.floor((Date.parse(item.startedAt) - startMs) / 86400e3);
      if (index >= 0 && index < days) buckets[index] += 1;
    }
    return buckets;
  }

  function sparklinePath(counts: number[], width = 320, height = 36): string {
    const max = Math.max(1, ...counts);
    const step = counts.length > 1 ? width / (counts.length - 1) : width;
    return counts
      .map((count, index) => {
        const x = index * step;
        const y = height - (count / max) * (height - 6) - 3;
        return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  }

  function presetLabel(preset: RepeatPreset): string {
    return t(`scheduledTasks.repeat.${preset}`);
  }

  function unitLabel(unit: IntervalUnit): string {
    return t(`scheduledTasks.unit.${unit}`);
  }

  function destinationLabel(value: Destination): string {
    return t(`scheduledTasks.destination.${value}`);
  }

  function openCreate() {
    createOpen = true;
  }

  function resetCreateForm() {
    taskName = "";
    userInput = "";
    repeatPreset = "none";
    intervalCount = 10;
    intervalUnit = "minutes";
    nextRunLocal = defaultNextRunLocal();
    destination = conversationDisabled ? "standalone" : "conversation";
  }

  async function createTask() {
    if (!tasksUrl) return;
    saving = true;
    try {
      const nextRunAt = new Date(nextRunLocal).toISOString();
      const intervalMs = intervalMsFromForm();
      await request(tasksUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          taskName,
          userInput,
          nextRunAt,
          ...(intervalMs ? { intervalMs } : { repeat: "none" }),
          destination,
          ...(destination === "conversation"
            ? { sessionPath, conversationId: sessionId }
            : {}),
        }),
      });
      createOpen = false;
      resetCreateForm();
      await loadTasks(tasksUrl);
    } catch {
      error = true;
    } finally {
      saving = false;
    }
  }

  async function patchTask(task: ScheduledTask, body: Record<string, unknown>) {
    if (!tasksUrl) return;
    try {
      await request(`${tasksUrl}/${encodeURIComponent(task.taskId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await loadTasks(tasksUrl);
    } catch {
      error = true;
    }
  }

  async function deleteTask(task: ScheduledTask) {
    if (!tasksUrl) return;
    try {
      await request(`${tasksUrl}/${encodeURIComponent(task.taskId)}`, {
        method: "DELETE",
      });
      await loadTasks(tasksUrl);
    } catch {
      error = true;
    }
  }

  async function runNow(task: ScheduledTask) {
    if (!tasksUrl) return;
    try {
      await request(`${tasksUrl}/${encodeURIComponent(task.taskId)}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      await loadTasks(tasksUrl);
    } catch {
      error = true;
    }
  }

  async function openTaskSession(task: ScheduledTask) {
    if (task.destination === "conversation" && task.sessionPath) {
      await onOpenSession(task.sessionPath);
      return;
    }
    const outputPath = runs.find(
      (run) => run.taskId === task.taskId && run.outputSessionPath,
    )?.outputSessionPath;
    if (outputPath) await onOpenSession(outputPath);
  }

  async function openRunSession(run: ScheduledRun) {
    if (run.outputSessionPath) await onOpenSession(run.outputSessionPath);
  }

  function defaultNextRunLocal() {
    const date = new Date(Date.now() + 60 * 60 * 1000);
    const pad = (value: number) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
</script>

<Dialog.Root {open} onOpenChange={(value) => { if (!value) onClose(); }}>
  <Dialog.Content
    class="bg-background flex h-[min(90dvh,52rem)] w-full flex-col gap-6 overflow-hidden p-8 sm:max-w-5xl sm:rounded-2xl"
    style={themeStyle}
    overlayProps={{ class: "bg-black/40", style: themeStyle }}
  >
    <Dialog.Header>
      <Dialog.Title class="sr-only">{t("scheduledTasks.title")}</Dialog.Title>
      <Dialog.Description class="sr-only">{t("scheduledTasks.description")}</Dialog.Description>
    </Dialog.Header>

    {#if !tasksUrl}
      <Alert.Root><Alert.Description>{t("scheduledTasks.disconnected")}</Alert.Description></Alert.Root>
    {:else if open}
      {#if error}
        <Alert.Root><Alert.Description>{t("scheduledTasks.error")}</Alert.Description></Alert.Root>
      {/if}

      <Tabs.Root bind:value={tab} class="min-h-0 flex-1 gap-8 overflow-hidden">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <Tabs.List class="rounded-full">
            <Tabs.Trigger class="rounded-full px-4" value="tasks">{t("scheduledTasks.tab.tasks")}</Tabs.Trigger>
            <Tabs.Trigger class="rounded-full px-4" value="runs">{t("scheduledTasks.tab.runs")}</Tabs.Trigger>
          </Tabs.List>
          <Button class="rounded-full px-4" type="button" onclick={() => openCreate()}>
            {t("scheduledTasks.createNew")}
          </Button>
        </div>

        <Tabs.Content value="tasks" class="min-h-0 overflow-y-auto">
          <div class="flex flex-col gap-5">
            <section class="bg-muted rounded-2xl px-5 py-4">
              <div class="flex items-start justify-between gap-3">
                <div class="flex min-w-0 items-baseline gap-2">
                  <h2 class="text-sm font-medium">{t("scheduledTasks.history.title")}</h2>
                  <p class="text-muted-foreground text-xs">{t("scheduledTasks.history.window")}</p>
                </div>
                <Button
                  class="text-muted-foreground"
                  variant="ghost"
                  size="sm"
                  type="button"
                  onclick={() => (tab = "runs")}
                >
                  {t("scheduledTasks.history.count", { count: recentRuns.length })}
                  <ChevronRight data-icon="inline-end" />
                </Button>
              </div>
              {#if historyHasRuns}
                <svg viewBox="0 0 320 36" class="text-muted-foreground mt-4 h-9 w-full" aria-hidden="true">
                  <path
                    d={sparklinePath(historyCounts)}
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              {:else}
                <div class="border-muted-foreground/35 mt-6 h-8 border-b border-dashed"></div>
              {/if}
            </section>

            <section class="bg-muted overflow-x-auto rounded-2xl px-5 py-4">
              <div class="min-w-[42rem] flex flex-col">
                <div class={`${TASK_GRID} text-muted-foreground px-1 text-xs`}>
                  <span>{t("scheduledTasks.columns.task")}</span>
                  <span>{t("scheduledTasks.columns.schedule")}</span>
                  <span>{t("scheduledTasks.columns.nextRun")}</span>
                  <span>{t("scheduledTasks.columns.status")}</span>
                  <span class="sr-only">{t("scheduledTasks.more")}</span>
                </div>
                {#if loading}
                  <div class="mt-4 flex flex-col gap-3">
                    <Skeleton class="h-14 w-full" />
                    <Skeleton class="h-14 w-full" />
                  </div>
                {:else if tasks.length === 0}
                  <p class="text-muted-foreground px-1 py-8 text-sm">{t("scheduledTasks.empty")}</p>
                {:else}
                  {#each tasks as task (task.taskId)}
                    <div class={`${TASK_GRID} items-center rounded-xl px-1 py-4`}>
                      <div class="flex min-w-0 items-center gap-3">
                        <div class="bg-background flex size-8 shrink-0 items-center justify-center rounded-lg [&_svg]:size-4">
                          <Sparkles aria-hidden="true" />
                        </div>
                        <span class="truncate font-medium">{task.taskName}</span>
                      </div>
                      <span class="text-muted-foreground truncate">{formatSchedule(task)}</span>
                      <span class="text-muted-foreground truncate">{formatNextRun(task.nextRunAt)}</span>
                      {#if task.enabled}
                        <span class="inline-flex items-center gap-1.5 text-sm" style="color: var(--success)">
                          <CircleCheck class="size-4" />
                          {t("scheduledTasks.status.enabled")}
                        </span>
                      {:else}
                        <span class="text-muted-foreground text-sm">{t("scheduledTasks.status.disabled")}</span>
                      {/if}
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger class={buttonVariants({ variant: "ghost", size: "icon-sm" })} type="button">
                          <EllipsisVertical />
                          <span class="sr-only">{t("scheduledTasks.more")}</span>
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Content align="end" class="w-40">
                          <DropdownMenu.Group>
                            <DropdownMenu.Item onSelect={() => void patchTask(task, { enabled: !task.enabled })}>
                              {task.enabled ? t("scheduledTasks.disable") : t("scheduledTasks.enable")}
                            </DropdownMenu.Item>
                            <DropdownMenu.Item onSelect={() => void runNow(task)}>
                              {t("scheduledTasks.runNow")}
                            </DropdownMenu.Item>
                            <DropdownMenu.Item onSelect={() => void openTaskSession(task)}>
                              {t("scheduledTasks.openSession")}
                            </DropdownMenu.Item>
                          </DropdownMenu.Group>
                          <DropdownMenu.Separator />
                          <DropdownMenu.Group>
                            <DropdownMenu.Item variant="destructive" onSelect={() => void deleteTask(task)}>
                              {t("scheduledTasks.delete")}
                            </DropdownMenu.Item>
                          </DropdownMenu.Group>
                        </DropdownMenu.Content>
                      </DropdownMenu.Root>
                    </div>
                  {/each}
                {/if}
              </div>
            </section>
          </div>
        </Tabs.Content>

        <Tabs.Content value="runs" class="min-h-0 overflow-y-auto">
          <section class="bg-muted overflow-x-auto rounded-2xl px-5 py-4">
            {#if loading}
              <div class="flex flex-col gap-3">
                <Skeleton class="h-14 w-full" />
                <Skeleton class="h-14 w-full" />
              </div>
            {:else if recentRuns.length === 0}
              <p class="text-muted-foreground py-8 text-sm">{t("scheduledTasks.runsEmptyTitle")}</p>
            {:else}
              <div class="min-w-[36rem] flex flex-col">
                <div class="text-muted-foreground grid grid-cols-[minmax(0,2fr)_minmax(8rem,1fr)_6rem_auto] gap-3 px-1 text-xs">
                  <span>{t("scheduledTasks.columns.task")}</span>
                  <span>{t("scheduledTasks.columns.started")}</span>
                  <span>{t("scheduledTasks.columns.result")}</span>
                  <span class="sr-only">{t("scheduledTasks.openSession")}</span>
                </div>
                {#each recentRuns as run (run.runId)}
                  <div class="grid grid-cols-[minmax(0,2fr)_minmax(8rem,1fr)_6rem_auto] items-center gap-3 rounded-xl px-1 py-4">
                    <div class="flex min-w-0 flex-col gap-1">
                      <span class="truncate font-medium">{run.taskName}</span>
                      <span class="text-muted-foreground text-xs">
                        {run.trigger === "manual" ? t("scheduledTasks.run.manual") : t("scheduledTasks.run.schedule")}
                      </span>
                    </div>
                    <span class="text-muted-foreground truncate">{formatDateTime(run.startedAt)}</span>
                    {#if run.status === "error"}
                      <Badge variant="destructive">{t("scheduledTasks.run.error")}</Badge>
                    {:else if run.status === "skipped"}
                      <Badge variant="outline">{t("scheduledTasks.run.skipped")}</Badge>
                    {:else}
                      <Badge variant="secondary">{t("scheduledTasks.run.ok")}</Badge>
                    {/if}
                    <Button
                      variant="ghost"
                      size="sm"
                      type="button"
                      disabled={!run.outputSessionPath}
                      onclick={() => void openRunSession(run)}
                    >
                      {t("scheduledTasks.openSession")}
                    </Button>
                  </div>
                {/each}
              </div>
            {/if}
          </section>
        </Tabs.Content>
      </Tabs.Root>
    {/if}
  </Dialog.Content>
</Dialog.Root>

<Dialog.Root bind:open={createOpen}>
  <Dialog.Content style={themeStyle} overlayProps={{ style: themeStyle }}>
    <Dialog.Header>
      <Dialog.Title>{t("scheduledTasks.createTitle")}</Dialog.Title>
      <Dialog.Description>{t("scheduledTasks.description")}</Dialog.Description>
    </Dialog.Header>
    <form
      class="flex flex-col gap-4"
      onsubmit={(event) => {
        event.preventDefault();
        void createTask();
      }}
    >
      <Field.Group>
        <Field.Field>
          <Field.Label for="scheduled-task-name">{t("scheduledTasks.name")}</Field.Label>
          <Input id="scheduled-task-name" bind:value={taskName} required />
        </Field.Field>
        <Field.Field>
          <Field.Label for="scheduled-task-action">{t("scheduledTasks.action")}</Field.Label>
          <Textarea id="scheduled-task-action" bind:value={userInput} required></Textarea>
        </Field.Field>
        <Field.Field>
          <Field.Label for="scheduled-task-next">{t("scheduledTasks.nextRun")}</Field.Label>
          <Input id="scheduled-task-next" type="datetime-local" bind:value={nextRunLocal} required />
        </Field.Field>
        <Field.Field>
          <Field.Label for="scheduled-task-repeat">{t("scheduledTasks.repeat")}</Field.Label>
          <Select.Root type="single" bind:value={repeatPreset}>
            <Select.Trigger id="scheduled-task-repeat" class="w-full">
              <span data-slot="select-value">{presetLabel(repeatPreset)}</span>
            </Select.Trigger>
            <Select.Content>
              <Select.Group>
                <Select.Item value="none" label={presetLabel("none")}>{presetLabel("none")}</Select.Item>
                <Select.Item value="custom" label={presetLabel("custom")}>{presetLabel("custom")}</Select.Item>
                <Select.Item value="hourly" label={presetLabel("hourly")}>{presetLabel("hourly")}</Select.Item>
                <Select.Item value="daily" label={presetLabel("daily")}>{presetLabel("daily")}</Select.Item>
                <Select.Item value="weekly" label={presetLabel("weekly")}>{presetLabel("weekly")}</Select.Item>
              </Select.Group>
            </Select.Content>
          </Select.Root>
        </Field.Field>
        {#if repeatPreset === "custom"}
          <Field.Field>
            <Field.Label for="scheduled-task-interval">{t("scheduledTasks.intervalValue")}</Field.Label>
            <div class="grid grid-cols-[minmax(0,1fr)_8rem] gap-2">
              <Input id="scheduled-task-interval" type="number" min="1" step="1" bind:value={intervalCount} required />
              <Select.Root type="single" bind:value={intervalUnit}>
                <Select.Trigger class="w-full">
                  <span data-slot="select-value">{unitLabel(intervalUnit)}</span>
                </Select.Trigger>
                <Select.Content>
                  <Select.Group>
                    <Select.Item value="seconds" label={unitLabel("seconds")}>{unitLabel("seconds")}</Select.Item>
                    <Select.Item value="minutes" label={unitLabel("minutes")}>{unitLabel("minutes")}</Select.Item>
                    <Select.Item value="hours" label={unitLabel("hours")}>{unitLabel("hours")}</Select.Item>
                    <Select.Item value="days" label={unitLabel("days")}>{unitLabel("days")}</Select.Item>
                    <Select.Item value="weeks" label={unitLabel("weeks")}>{unitLabel("weeks")}</Select.Item>
                  </Select.Group>
                </Select.Content>
              </Select.Root>
            </div>
          </Field.Field>
        {/if}
        <Field.Field>
          <Field.Label for="scheduled-task-destination">{t("scheduledTasks.destination")}</Field.Label>
          <Select.Root type="single" bind:value={destination}>
            <Select.Trigger id="scheduled-task-destination" class="w-full">
              <span data-slot="select-value">{destinationLabel(destination)}</span>
            </Select.Trigger>
            <Select.Content>
              <Select.Group>
                <Select.Item
                  value="conversation"
                  label={destinationLabel("conversation")}
                  disabled={conversationDisabled}
                >
                  {destinationLabel("conversation")}
                </Select.Item>
                <Select.Item value="standalone" label={destinationLabel("standalone")}>
                  {destinationLabel("standalone")}
                </Select.Item>
              </Select.Group>
            </Select.Content>
          </Select.Root>
          {#if conversationDisabled}
            <Field.Description>{t("scheduledTasks.needSession")}</Field.Description>
          {/if}
        </Field.Field>
      </Field.Group>
      <Dialog.Footer>
        <Button variant="outline" type="button" onclick={() => (createOpen = false)}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" disabled={saving}>{t("scheduledTasks.create")}</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>
