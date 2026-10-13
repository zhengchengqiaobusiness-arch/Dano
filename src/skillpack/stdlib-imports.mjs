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

const PYTHON_BINS = ["python3", "python"];

const STDLIB = new Set([
  "abc", "argparse", "ast", "asyncio", "base64", "binascii", "calendar", "collections",
  "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal", "email",
  "enum", "errno", "fnmatch", "functools", "glob", "gzip", "hashlib", "hmac", "html",
  "http", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "logging",
  "math", "mimetypes", "numbers", "operator", "os", "pathlib", "pickle", "platform",
  "pprint", "queue", "random", "re", "secrets", "shlex", "shutil", "signal", "socket",
  "sqlite3", "ssl", "stat", "string", "struct", "subprocess", "sys", "tempfile", "textwrap",
  "threading", "time", "traceback", "types", "typing", "unicodedata", "urllib", "uuid",
  "warnings", "weakref", "xml", "zipfile", "zlib",
]);

const IMPORT_LINE = /^(?:from[ \t]+([A-Za-z_][\w.]*)[ \t]+import|import[ \t]+([A-Za-z_][\w.]*))/gm;

export function jsNonStdlibImports(source) {
  const names = new Set();
  const text = String(source || "");
  for (const match of text.matchAll(IMPORT_LINE)) {
    const raw = match[1] || match[2] || "";
    const name = raw.split(".")[0];
    if (name && !STDLIB.has(name)) names.add(name);
  }
  return [...names].sort();
}

function spawnProbe(bin, source) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, ["-c", PROBE], { stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(err.trim() || `${bin} exited ${code}`));
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

export async function nonStdlibImports(source) {
  for (const bin of PYTHON_BINS) {
    try {
      return await spawnProbe(bin, source);
    } catch {
      // 这份环境没有这个解释器名就换下一个；都没有再用静态扫描，不要把录制机缺二进制写成脚本不可调用
    }
  }
  return { imports: jsNonStdlibImports(source) };
}
