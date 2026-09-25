import { spawn } from "node:child_process";
import { constants, existsSync } from "node:fs";
import {
  createBashTool,
  createEditTool,
  createReadTool,
  createWriteTool,
} from "@earendil-works/pi-coding-agent";

const profile = process.env.SYKE_TOOL_SANDBOX_PROFILE;
if (!profile) {
  throw new Error("SYKE_TOOL_SANDBOX_PROFILE is required for Syke model tools");
}

const modelToolEnvKeys = [
  "HOME",
  "PATH",
  "TMPDIR",
  "TMP",
  "TEMP",
  "USER",
  "LOGNAME",
  "LANG",
  "LC_ALL",
  "SHELL",
  "TERM",
  "COLORTERM",
  "NO_COLOR",
  "FORCE_COLOR",
];

function modelToolEnvironment(source = process.env) {
  const env = {};
  for (const key of modelToolEnvKeys) {
    if (source[key]) env[key] = source[key];
  }
  return env;
}

const workerScript = String.raw`
import { constants } from "node:fs";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));

function imageMime(buffer) {
  if (buffer.length >= 8 && buffer.subarray(0, 8).equals(
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  )) return "image/png";
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) {
    return "image/jpeg";
  }
  const head = buffer.subarray(0, 6).toString("ascii");
  if (head === "GIF87a" || head === "GIF89a") return "image/gif";
  if (
    buffer.length >= 12
    && buffer.subarray(0, 4).toString("ascii") === "RIFF"
    && buffer.subarray(8, 12).toString("ascii") === "WEBP"
  ) return "image/webp";
  return null;
}

try {
  let result = {};
  if (request.op === "readFile") {
    const data = await readFile(request.path);
    result = { data: data.toString("base64") };
  } else if (request.op === "access") {
    await access(request.path, request.mode ?? constants.R_OK);
  } else if (request.op === "detectImageMimeType") {
    result = { mime: imageMime(await readFile(request.path)) };
  } else if (request.op === "writeFile") {
    await writeFile(request.path, request.content, "utf8");
  } else if (request.op === "mkdir") {
    await mkdir(request.path, { recursive: true });
  } else {
    throw new Error("Unknown Syke tool operation: " + request.op);
  }
  process.stdout.write(JSON.stringify({ ok: true, ...result }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: String(error && error.message ? error.message : error),
  }));
  process.exitCode = 1;
}
`;

function runFileOperation(request) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "/usr/bin/sandbox-exec",
      ["-f", profile, process.execPath, "--input-type=module", "-e", workerScript],
      {
        cwd: process.cwd(),
        env: modelToolEnvironment(),
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      const output = Buffer.concat(stdout).toString("utf8");
      let response;
      try {
        response = JSON.parse(output);
      } catch {
        const detail = Buffer.concat(stderr).toString("utf8").trim();
        reject(new Error(detail || `Sandboxed file operation exited ${code}`));
        return;
      }
      if (code !== 0 || !response.ok) {
        const detail = Buffer.concat(stderr).toString("utf8").trim();
        reject(new Error(response.error || detail || `Sandboxed file operation exited ${code}`));
        return;
      }
      resolve(response);
    });
    child.stdin.end(JSON.stringify(request));
  });
}

const readOperations = {
  readFile: async (path) => {
    const response = await runFileOperation({ op: "readFile", path });
    return Buffer.from(response.data, "base64");
  },
  access: async (path) => {
    await runFileOperation({ op: "access", path, mode: constants.R_OK });
  },
  detectImageMimeType: async (path) => {
    const response = await runFileOperation({ op: "detectImageMimeType", path });
    return response.mime;
  },
};

const writeOperations = {
  writeFile: async (path, content) => {
    await runFileOperation({ op: "writeFile", path, content });
  },
  mkdir: async (path) => {
    await runFileOperation({ op: "mkdir", path });
  },
};

const editOperations = {
  readFile: readOperations.readFile,
  writeFile: writeOperations.writeFile,
  access: async (path) => {
    await runFileOperation({
      op: "access",
      path,
      mode: constants.R_OK | constants.W_OK,
    });
  },
};

function modelToolShell() {
  // Pi's documented Unix default: /bin/bash, then sh. Pin the sandbox shell
  // instead of inheriting $SHELL (the user's login shell, e.g. zsh, whose
  // heredoc scratch ignores TMPDIR and defaults to /tmp/zsh outside the
  // writable boundary). bash heredocs honor TMPDIR, which child_env already
  // points at the swept runtime/tmp subtree.
  if (existsSync("/bin/bash")) return "/bin/bash";
  return "/bin/sh";
}

const sandboxedBashCommand = [
  "exec /usr/bin/sandbox-exec",
  '-f "$SYKE_TOOL_SANDBOX_PROFILE"',
  '"$SYKE_TOOL_SHELL" -c "$SYKE_TOOL_COMMAND"',
].join(" ");

function sandboxedBashSpawn({ command, cwd, env }) {
  // Keep the model command opaque until the sandboxed shell receives it.
  return {
    command: sandboxedBashCommand,
    cwd,
    env: {
      ...modelToolEnvironment(env),
      SYKE_TOOL_SANDBOX_PROFILE: profile,
      SYKE_TOOL_SHELL: modelToolShell(),
      SYKE_TOOL_COMMAND: command,
    },
  };
}

export default function registerSykeTools(pi) {
  const cwd = process.cwd();
  pi.registerTool(createReadTool(cwd, { operations: readOperations }));
  pi.registerTool(
    createBashTool(cwd, {
      shellPath: "/bin/sh",
      spawnHook: sandboxedBashSpawn,
    }),
  );
  pi.registerTool(createEditTool(cwd, { operations: editOperations }));
  pi.registerTool(createWriteTool(cwd, { operations: writeOperations }));
}
