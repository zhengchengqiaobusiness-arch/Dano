import { spawn } from "node:child_process";

const PROBE = `
import ast, json, sys
source = sys.stdin.read()
try:
    tree = ast.parse(source)
except SyntaxError as exc:
    print(json.dumps({"syntax": str(exc)}))
    raise SystemExit(0)
names = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.extend(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
        names.append(node.module.split(".")[0])
std = set(getattr(sys, "stdlib_module_names", ()))
print(json.dumps({"imports": sorted({name for name in names if name and name not in std})}))
`;

export function nonStdlibImports(source) {
  return new Promise((resolve, reject) => {
    const child = spawn("python", ["-c", PROBE], { stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(err.trim() || `python exited ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(out));
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(String(source || ""));
  });
}
