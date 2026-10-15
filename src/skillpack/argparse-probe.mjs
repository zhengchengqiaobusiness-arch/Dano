import { spawn } from "node:child_process";
import path from "node:path";

const PROBE = `
import argparse, json, runpy, sys
def grab(self, *args, **kwargs):
    out = {}
    for action in self._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                out[name] = any("--confirm" in act.option_strings for act in sub._actions)
    print(json.dumps(out))
    raise SystemExit(0)
argparse.ArgumentParser.parse_args = grab
sys.argv = ["client.py"]
runpy.run_path("scripts/client.py", run_name="__main__")
`;

const PYTHON_BINS = ["python3", "python"];

function run(bin, cwd) {
  return new Promise((resolve) => {
    const child = spawn(bin, ["-c", PROBE], { cwd, env: { ...process.env, DANO_AUTH_HEADERS: "" } });
    let out = "";
    const timer = setTimeout(() => { child.kill(); resolve(null); }, 5000);
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.on("error", () => { clearTimeout(timer); resolve(null); });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) return resolve(null);
      try {
        const last = out.trim().split("\n").at(-1);
        resolve(JSON.parse(last));
      } catch {
        resolve(null);
      }
    });
  });
}

export async function declaredSubcommands(skillDir) {
  for (const bin of PYTHON_BINS) {
    const found = await run(bin, path.resolve(skillDir));
    if (found && typeof found === "object") return found;
  }
  return null;
}
