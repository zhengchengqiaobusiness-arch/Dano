#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const fixturePath = resolve(root, "docs/research/fixtures/issue477-evaluation.json");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function json(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

try {
  const evidencePaths = process.argv.slice(2);
  assert(evidencePaths.length > 0, "usage: audit-memory-evaluation-coverage.mjs <evidence.json> [...]");
  const fixture = await json(fixturePath);
  const repetitions = fixture.execution?.repetitionsPerCase;
  assert(Number.isSafeInteger(repetitions) && repetitions > 0, "invalid frozen repetition count");
  assert(Array.isArray(fixture.cases) && fixture.cases.length > 0, "invalid frozen cases");

  const cases = new Map();
  for (const item of fixture.cases) {
    assert(typeof item.id === "string" && typeof item.category === "string"
      && !cases.has(item.id), "invalid or duplicate frozen case");
    cases.set(item.id, item);
  }

  const observed = new Map();
  const sources = [];
  for (const path of evidencePaths) {
    const evidence = await json(resolve(path));
    assert(Array.isArray(evidence.records), `evidence has no records: ${path}`);
    let count = 0;
    for (const record of evidence.records) {
      assert(cases.has(record.caseId), `unknown case ID in ${path}`);
      assert(Number.isSafeInteger(record.repetition)
        && record.repetition >= 1 && record.repetition <= repetitions,
      `invalid repetition in ${path}`);
      assert(typeof record.passed === "boolean", `missing reported result in ${path}`);
      const key = `${record.caseId}/${record.repetition}`;
      assert(!observed.has(key), `duplicate case attempt: ${key}`);
      observed.set(key, record.passed);
      count++;
    }
    sources.push({ path, server: evidence.summary?.server ?? "unspecified", records: count });
  }

  const categories = {};
  for (const item of cases.values()) {
    const entry = categories[item.category] ??= {
      cases: 0, expectedAttempts: 0, observedAttempts: 0,
      reportedPasses: 0, reportedFailures: 0, missingAttempts: 0,
    };
    entry.cases++;
    entry.expectedAttempts += repetitions;
    for (let repetition = 1; repetition <= repetitions; repetition++) {
      const result = observed.get(`${item.id}/${repetition}`);
      if (result === undefined) entry.missingAttempts++;
      else {
        entry.observedAttempts++;
        if (result) entry.reportedPasses++;
        else entry.reportedFailures++;
      }
    }
  }
  const totals = Object.values(categories).reduce((sum, entry) => ({
    expectedAttempts: sum.expectedAttempts + entry.expectedAttempts,
    observedAttempts: sum.observedAttempts + entry.observedAttempts,
    reportedPasses: sum.reportedPasses + entry.reportedPasses,
    reportedFailures: sum.reportedFailures + entry.reportedFailures,
    missingAttempts: sum.missingAttempts + entry.missingAttempts,
  }), { expectedAttempts: 0, observedAttempts: 0, reportedPasses: 0,
    reportedFailures: 0, missingAttempts: 0 });
  const complete = totals.missingAttempts === 0 && totals.reportedFailures === 0;
  process.stdout.write(JSON.stringify({
    purpose: "attempt-coverage-only",
    status: complete ? "reported-complete" : "incomplete",
    note: "Reported results and source labels are not independently verified; this does not establish Dano, model-answer, Browser, latency, cost, or release acceptance.",
    fixture: fixturePath,
    sources,
    totals,
    categories,
  }, null, 2) + "\n");
  if (!complete) process.exitCode = 1;
} catch (error) {
  process.stderr.write(`coverage audit failed: ${error instanceof Error ? error.message : "unknown error"}\n`);
  process.exitCode = 1;
}
