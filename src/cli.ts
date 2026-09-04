#!/usr/bin/env node
import { StudioService } from "./studio-service.js";

function parseArgs(argv: string[]) {
  const args = [...argv];
  const command = args.shift();
  const flags = new Map<string, string | boolean>();
  const positional: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const token = args[i]!;
    if (token.startsWith("--")) {
      const next = args[i + 1];
      const key = token.slice(2);
      if (next && !next.startsWith("--")) {
        const previous = flags.get(key);
        flags.set(key, typeof previous === "string" ? `${previous},${next}` : next);
        i++;
      } else flags.set(key, true);
    } else positional.push(token);
  }
  return { command, flags, positional };
}

function flag(flags: Map<string, string | boolean>, name: string) {
  const value = flags.get(name);
  return typeof value === "string" ? value : undefined;
}

function flagList(flags: Map<string, string | boolean>, name: string) {
  return (flag(flags, name) || "").split(",").map(item => item.trim()).filter(Boolean);
}

async function main() {
  const { command, flags, positional } = parseArgs(process.argv.slice(2));
  const studio = new StudioService();

  switch (command) {
    case "record": {
      const url = flag(flags, "url") || positional[0];
      if (!url) throw new Error("record requires --url <business-system-url>");
      const session = await studio.startRecording(url, flag(flags, "name"));
      console.log(JSON.stringify(session, null, 2));
      console.log("\nBrowser recording is active. Operate the real system, then press Ctrl+C to stop.");
      let stopping = false;
      const stop = async () => {
        if (stopping) return;
        stopping = true;
        const done = await studio.stopRecording();
        console.log(`\nSaved recording ${done.id}`);
        process.exit(0);
      };
      process.on("SIGINT", () => void stop());
      process.on("SIGTERM", () => void stop());
      await new Promise(() => {});
      break;
    }
    case "sessions":
      console.log(JSON.stringify(await studio.listSessions(), null, 2));
      break;
    case "analyze": {
      const caps = await studio.analyze(flag(flags, "session"), !flags.has("no-llm"));
      console.log(JSON.stringify({ count: caps.length, capabilities: caps.map(c => ({ id: c.id, title: c.title, operation: c.operation })) }, null, 2));
      break;
    }
    case "validate": {
      const { capabilities, review } = await studio.review(flag(flags, "session"));
      console.log(JSON.stringify({
        status: review.status,
        next: review.next,
        summary: review.summary,
        findings: review.findings,
        capabilities: capabilities.map(item => ({ id: item.id, title: item.title, status: item.validation.status, checks: item.validation.checks }))
      }, null, 2));
      if (review.status !== "passed") process.exitCode = 2;
      break;
    }
    case "bind": {
      if (!flags.has("approve")) throw new Error("bind requires --approve to record explicit human confirmation");
      const fromCapabilityId = flag(flags, "from");
      const fromPath = flag(flags, "from-path");
      const toCapabilityId = flag(flags, "to");
      const toPath = flag(flags, "to-path");
      if (!fromCapabilityId || !fromPath || !toCapabilityId || !toPath) {
        throw new Error("bind requires --from, --from-path, --to, --to-path");
      }
      console.log(JSON.stringify(await studio.approveBinding({
        fromCapabilityId, fromPath, toCapabilityId, toPath, note: flag(flags, "note")
      }), null, 2));
      break;
    }
    case "candidate-source": {
      if (!flags.has("approve")) throw new Error("candidate-source requires --approve");
      const targetCapabilityId = flag(flags, "target");
      const inputPath = flag(flags, "field");
      const sourceCapabilityId = flag(flags, "source");
      const valuePath = flag(flags, "value-path");
      const labelPath = flag(flags, "label-path");
      if (!targetCapabilityId || !inputPath || !sourceCapabilityId || !valuePath || !labelPath) {
        throw new Error("candidate-source requires --target --field --source --value-path --label-path");
      }
      console.log(JSON.stringify(await studio.setDynamicCandidates({
        targetCapabilityId,
        inputPath,
        sourceCapabilityId,
        valuePath,
        labelPath,
        dependsOn: (flag(flags, "depends-on") || "").split(",").filter(Boolean)
      }), null, 2));
      break;
    }
    case "plan": {
      const goal = positional.join(" ") || flag(flags, "goal");
      if (!goal) throw new Error("plan requires a natural-language goal");
      console.log(JSON.stringify(await studio.plan(goal), null, 2));
      break;
    }
    case "execute": {
      const capabilityId = flag(flags, "capability");
      if (!capabilityId) throw new Error("execute requires --capability <id>");
      const input = JSON.parse(flag(flags, "input") || "{}");
      console.log(JSON.stringify(await studio.execute(capabilityId, input, flags.has("confirm-write")), null, 2));
      break;
    }
    case "export": {
      const name = flag(flags, "name") || positional[0];
      if (!name) throw new Error("export requires --name <skill-name>");
      console.log(JSON.stringify(await studio.export(name, flag(flags, "out"), flagList(flags, "match"), flag(flags, "session")), null, 2));
      break;
    }
    default:
      console.log(`pi-business-skill-studio

Commands:
  record   --url <url> [--name <name>]
  sessions
  analyze  [--session <id>] [--no-llm]
  validate [--session <id>]   # 审核门禁：通过才能导出；未通过会给出回溯阶段
  bind     --from <cap> --from-path <jsonpath> --to <cap> --to-path <jsonpath> --approve
  candidate-source --target <cap> --field <path> --source <query-cap> --value-path <path> --label-path <path> --approve
  plan     <natural language goal>
  execute  --capability <id> --input '<json>' [--confirm-write]
  export   --name <skill-name> [--out <directory>] [--match <path-or-id>] [--session <id>]
`);
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
