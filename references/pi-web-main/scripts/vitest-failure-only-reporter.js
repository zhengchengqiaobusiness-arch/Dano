import { relative } from "node:path";

function collectTests(children, tests = []) {
  for (const child of children) {
    if (child.type === "test") {
      tests.push(child);
      continue;
    }

    collectTests(child.children, tests);
  }

  return tests;
}

function formatDuration(durationMs) {
  if (durationMs >= 1000) {
    return `${(durationMs / 1000).toFixed(2)}s`;
  }

  return `${Math.round(durationMs)}ms`;
}

function summarizeTests(testModules) {
  const summary = {
    totalFiles: testModules.length,
    failedFiles: 0,
    passedFiles: 0,
    skippedFiles: 0,
    totalTests: 0,
    failedTests: 0,
    passedTests: 0,
    skippedTests: 0,
  };

  for (const testModule of testModules) {
    const state = testModule.state();
    if (state === "failed") {
      summary.failedFiles += 1;
    } else if (state === "passed") {
      summary.passedFiles += 1;
    } else if (state === "skipped") {
      summary.skippedFiles += 1;
    }

    for (const test of collectTests(testModule.children)) {
      summary.totalTests += 1;

      const testState = test.result().state;
      if (testState === "failed") {
        summary.failedTests += 1;
      } else if (testState === "passed") {
        summary.passedTests += 1;
      } else if (testState === "skipped") {
        summary.skippedTests += 1;
      }
    }
  }

  return summary;
}

function formatStateLine(kind, passed, failed, skipped, total) {
  const parts = [];

  if (failed > 0) {
    parts.push(`${failed} failed`);
  }

  if (passed > 0) {
    parts.push(`${passed} passed`);
  }

  if (skipped > 0) {
    parts.push(`${skipped} skipped`);
  }

  if (parts.length === 0) {
    parts.push(`0 ${kind}`);
  }

  return `${parts.join(", ")} (${total})`;
}

export default class FailureOnlyReporter {
  constructor() {
    this.ctx = null;
    this.startedAt = 0;
  }

  onInit(ctx) {
    this.ctx = ctx;
    this.startedAt = performance.now();
    ctx.logger.printBanner();
  }

  onTestRunStart() {
    this.startedAt = performance.now();
  }

  onTestRunEnd(testModules, unhandledErrors) {
    for (const testModule of testModules) {
      this.printModuleFailures(testModule);
    }

    if (unhandledErrors.length > 0) {
      this.ctx.logger.printUnhandledErrors([...unhandledErrors]);
      this.ctx.logger.error();
    }

    this.printSummary(testModules, unhandledErrors.length);
  }

  printModuleFailures(testModule) {
    const moduleFailures = testModule.errors();
    const failedTests = collectTests(testModule.children).filter(
      test => test.result().state === "failed",
    );

    if (moduleFailures.length === 0 && failedTests.length === 0) {
      return;
    }

    const relativePath = relative(this.ctx.config.root, testModule.moduleId);

    for (const error of moduleFailures) {
      this.ctx.logger.error(`\nFAIL ${relativePath}`);
      this.ctx.logger.printError(error, {
        project: testModule.project,
        showCodeFrame: true,
      });
    }

    for (const test of failedTests) {
      this.ctx.logger.error(`\nFAIL ${relativePath} > ${test.fullName}`);

      for (const error of test.result().errors) {
        this.ctx.logger.printError(error, {
          project: test.project,
          showCodeFrame: true,
        });
      }
    }
  }

  printSummary(testModules, unhandledErrorCount) {
    const summary = summarizeTests(testModules);
    const durationMs = performance.now() - this.startedAt;

    this.ctx.logger.log();
    this.ctx.logger.log(
      `Test Files  ${formatStateLine("files", summary.passedFiles, summary.failedFiles, summary.skippedFiles, summary.totalFiles)}`,
    );
    this.ctx.logger.log(
      `Tests       ${formatStateLine("tests", summary.passedTests, summary.failedTests, summary.skippedTests, summary.totalTests)}`,
    );

    if (unhandledErrorCount > 0) {
      this.ctx.logger.log(
        `Errors      ${unhandledErrorCount} error${unhandledErrorCount === 1 ? "" : "s"}`,
      );
    }

    this.ctx.logger.log(`Duration    ${formatDuration(durationMs)}`);
    this.ctx.logger.log();
  }
}
