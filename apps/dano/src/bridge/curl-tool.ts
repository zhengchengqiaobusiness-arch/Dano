import { spawn } from "node:child_process";
import {
  defineTool,
  type AgentToolResult,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export const curlToolParameters = Type.Object({
  args: Type.Array(Type.String()),
});

interface CurlToolDetails {
  stderr: string;
  exitCode: number | null;
}

export function createCurlTool(cwd: string) {
  return defineTool({
    name: "curl",
    label: "Curl",
    description: "Run curl with the provided CLI arguments.",
    promptSnippet: "Run curl without exposing a shell",
    promptGuidelines: [
      "Omit the curl executable name and pass each CLI argument as one args item without shell quote characters or shell operators.",
      "Use curl's own options for files and output; relative paths resolve from the active Agent workspace.",
    ],
    parameters: curlToolParameters,
    executionMode: "sequential",
    execute(_toolCallId, { args }, signal) {
      return new Promise<AgentToolResult<CurlToolDetails>>((resolve, reject) => {
        const child = spawn("curl", args, {
          cwd,
          env: process.env,
          shell: false,
          stdio: ["ignore", "pipe", "pipe"],
        });
        let stdout = "";
        let stderr = "";

        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stdout.on("data", chunk => (stdout += chunk));
        child.stderr.on("data", chunk => (stderr += chunk));

        const abort = () => child.kill();
        signal?.addEventListener("abort", abort, { once: true });

        child.once("error", error => {
          signal?.removeEventListener("abort", abort);
          reject(error);
        });
        child.once("close", exitCode => {
          signal?.removeEventListener("abort", abort);
          resolve({
            content: [{ type: "text", text: stdout || stderr }],
            details: { stderr, exitCode },
          });
        });

        if (signal?.aborted) abort();
      });
    },
  });
}
