import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  stat,
} from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import {
  ensureExecutable,
  isMain,
  prependCargoBin,
  repoRoot,
  resolveRustc,
  sidecarPathForTarget,
  validateTargetTriple,
} from "./lib/platform.mjs";

function parseArguments(argv) {
  const options = { binary: undefined, timeoutSeconds: 30 };
  const nextValue = (name, index) => {
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${name} requires a value.`);
    }
    return value;
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--binary") {
      options.binary = path.resolve(nextValue(argument, index));
      index += 1;
    } else if (argument === "--timeout") {
      options.timeoutSeconds = Number(nextValue(argument, index));
      index += 1;
      if (
        !Number.isInteger(options.timeoutSeconds) ||
        options.timeoutSeconds < 5 ||
        options.timeoutSeconds > 300
      ) {
        throw new Error("--timeout must be an integer from 5 through 300.");
      }
    } else {
      throw new Error(`Unknown sidecar smoke-test argument: ${argument}`);
    }
  }
  return options;
}

function capture(command, args, env) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    env,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error(`${command} failed while resolving the Rust target.`);
  }
  return result.stdout.trim();
}

export async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Unable to allocate a loopback smoke-test port."));
        return;
      }
      const { port } = address;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function safeTempDirectory(directory) {
  const relative = path.relative(path.resolve(os.tmpdir()), path.resolve(directory));
  const leaf = path.basename(directory);
  return (
    relative &&
    !relative.startsWith("..") &&
    !path.isAbsolute(relative) &&
    leaf.startsWith("project-master-sidecar-")
  );
}

export function appendBounded(current, chunk) {
  const next = `${current}${chunk}`;
  return next.length > 64_000 ? next.slice(-64_000) : next;
}

export function cleanChildEnvironment(environment) {
  return Object.fromEntries(
    Object.entries(environment).filter(([name]) => {
      const normalized = name.toUpperCase();
      if (normalized.startsWith("MASTER_")) return false;
      if (
        /^(?:HTTP|HTTPS|ALL|FTP|NO)_PROXY$/.test(normalized) ||
        normalized.startsWith("HF_") ||
        normalized.startsWith("HUGGINGFACE_") ||
        normalized.startsWith("TRANSFORMERS_")
      ) {
        return false;
      }
      return !/(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIALS?|COOKIE|AUTH)(?:$|_)/.test(
        normalized,
      );
    }),
  );
}

export function redactSecret(value, secret) {
  return secret ? String(value).split(secret).join("[REDACTED]") : String(value);
}

export async function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  await new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.off("exit", finish);
      resolve();
    };
    const timer = setTimeout(finish, timeoutMs);
    child.once("exit", finish);
  });
}

export function processGroupIsRunning(pid) {
  if (process.platform === "win32" || !Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    if (error?.code === "EPERM") return true;
    throw error;
  }
}

async function waitForProcessGroupExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (processGroupIsRunning(pid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return !processGroupIsRunning(pid);
}

export async function stopProcessTree(child, gracefulTimeoutMs = 5_000) {
  if (!child.pid) {
    return { signal: "not_started", forced: false };
  }
  let signal = "SIGTERM";
  let forced = false;
  if (process.platform === "win32") {
    if (child.exitCode !== null || child.signalCode !== null) {
      return { signal: "already_exited", forced: false };
    }
    const result = spawnSync(
      "taskkill",
      ["/PID", String(child.pid), "/T", "/F"],
      { windowsHide: true, stdio: "ignore" },
    );
    signal = "taskkill";
    forced = true;
    if (result.status !== 0 && child.exitCode === null) {
      child.kill();
    }
  } else {
    const leaderExited =
      child.exitCode !== null || child.signalCode !== null;
    const groupRunning = processGroupIsRunning(child.pid);
    if (!groupRunning && leaderExited) {
      return { signal: "already_exited", forced: false };
    }

    if (groupRunning) {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch (error) {
        if (error?.code !== "ESRCH") throw error;
      }
    } else {
      child.kill("SIGTERM");
    }

    const gracefullyStopped = groupRunning
      ? await waitForProcessGroupExit(child.pid, gracefulTimeoutMs)
      : (await waitForExit(child, gracefulTimeoutMs),
        child.exitCode !== null || child.signalCode !== null);
    if (!gracefullyStopped) {
      signal = "SIGKILL";
      forced = true;
      if (processGroupIsRunning(child.pid)) {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch (error) {
          if (error?.code !== "ESRCH") throw error;
        }
      } else if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
    }
    const groupStopped = await waitForProcessGroupExit(child.pid, 5_000);
    if (!groupStopped) {
      throw new Error(
        `Process group ${child.pid} survived SIGKILL during sidecar cleanup.`,
      );
    }
  }
  await waitForExit(child, 5_000);
  return { signal, forced };
}

export function sessionHeaders(sessionToken) {
  return {
    "X-Project-Master-Token": sessionToken,
  };
}

export async function fetchJson(url, options = {}) {
  const response = await fetch(url, { ...options, redirect: "error" });
  const payload = await response.json().catch(() => null);
  return { response, payload };
}

export async function waitForReady(port, child, sessionToken, deadline) {
  let lastError;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(
        `Backend sidecar exited early with ${child.exitCode ?? child.signalCode}.`,
      );
    }
    try {
      const { response, payload } = await fetchJson(
        `http://127.0.0.1:${port}/api/v1/ready`,
        {
          headers: sessionHeaders(sessionToken),
          signal: AbortSignal.timeout(2_000),
        },
      );
      if (response.ok && payload?.ok === true) {
        return payload;
      }
      lastError = new Error(
        `Readiness endpoint returned HTTP ${response.status}.`,
      );
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `Backend sidecar did not become ready: ${lastError?.message ?? "timeout"}`,
  );
}

export async function authenticatedJson(
  port,
  route,
  sessionToken,
  timeoutMs = 10_000,
) {
  return fetchJson(`http://127.0.0.1:${port}${route}`, {
    headers: sessionHeaders(sessionToken),
    signal: AbortSignal.timeout(timeoutMs),
  });
}

async function loadSchema(port, sessionToken) {
  const { response, payload } = await fetchJson(
    `http://127.0.0.1:${port}/openapi.json`,
    {
      headers: sessionHeaders(sessionToken),
      signal: AbortSignal.timeout(2_000),
    },
  );
  if (!response.ok) {
    throw new Error(`OpenAPI endpoint returned HTTP ${response.status}.`);
  }
  return payload;
}

export async function waitForPortClosed(port, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const open = await new Promise((resolve) => {
      const socket = net.createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => resolve(false));
      socket.setTimeout(500, () => {
        socket.destroy();
        resolve(false);
      });
    });
    if (!open) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Backend sidecar left its loopback port open after shutdown.");
}

export async function testBackendSidecar(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  const env = prependCargoBin();
  const rustc = resolveRustc(env);
  const targetTriple = validateTargetTriple(
    capture(rustc, ["--print", "host-tuple"], env),
  );
  const binary = options.binary ?? sidecarPathForTarget(repoRoot, targetTriple);
  ensureExecutable(binary);
  const binaryStats = await stat(binary);
  if (!binaryStats.isFile() || binaryStats.size === 0) {
    throw new Error(`Backend sidecar is empty or not a file: ${binary}`);
  }

  const sessionToken = randomBytes(32).toString("hex");
  let testRoot;
  let port;
  let logPath;
  let child;
  let stdout = "";
  let stderr = "";
  let failure;
  let interruptedSignal;
  const interruptHandlers = new Map(
    ["SIGINT", "SIGTERM"].map((signal) => [
      signal,
      () => {
        const repeated = Boolean(interruptedSignal);
        interruptedSignal ??= signal;
        if (child?.pid) {
          const terminationSignal = repeated ? "SIGKILL" : "SIGTERM";
          try {
            if (process.platform === "win32") {
              const result = spawnSync(
                "taskkill",
                ["/PID", String(child.pid), "/T", "/F"],
                { windowsHide: true, stdio: "ignore" },
              );
              if (result.status !== 0 && child.exitCode === null) {
                child.kill();
              }
            } else {
              process.kill(-child.pid, terminationSignal);
            }
          } catch (error) {
            if (error?.code !== "ESRCH") {
              console.error(`Unable to signal sidecar: ${error.message}`);
            }
          }
        }
      },
    ]),
  );
  for (const [signal, handler] of interruptHandlers) {
    process.on(signal, handler);
  }
  try {
    testRoot = await mkdtemp(
      path.join(os.tmpdir(), "project-master-sidecar-"),
    );
    if (!safeTempDirectory(testRoot)) {
      throw new Error(`Refusing to use unexpected test directory: ${testRoot}`);
    }
    if (interruptedSignal) throw new Error(`Interrupted by ${interruptedSignal}.`);
    port = await freePort();
    const offlineOllamaPort = await freePort();
    if (interruptedSignal) throw new Error(`Interrupted by ${interruptedSignal}.`);
    logPath = path.join(testRoot, "backend.log");
    const isolatedEnvironment = {
      home: path.join(testRoot, "home"),
      data: path.join(testRoot, "data"),
      config: path.join(testRoot, "config"),
      cache: path.join(testRoot, "cache"),
      temp: path.join(testRoot, "tmp"),
    };
    await Promise.all(
      Object.values(isolatedEnvironment).map((directory) =>
        mkdir(directory, { recursive: true })
      ),
    );
    child = spawn(binary, [], {
      cwd: testRoot,
      detached: process.platform !== "win32",
      windowsHide: true,
      env: {
        ...cleanChildEnvironment(env),
        NO_PROXY: "127.0.0.1,localhost,::1",
        no_proxy: "127.0.0.1,localhost,::1",
        HOME: isolatedEnvironment.home,
        USERPROFILE: isolatedEnvironment.home,
        XDG_DATA_HOME: isolatedEnvironment.data,
        XDG_CONFIG_HOME: isolatedEnvironment.config,
        XDG_CACHE_HOME: isolatedEnvironment.cache,
        TMPDIR: isolatedEnvironment.temp,
        TEMP: isolatedEnvironment.temp,
        TMP: isolatedEnvironment.temp,
        PYTHONDONTWRITEBYTECODE: "1",
        MASTER_API_PORT: String(port),
        MASTER_CONFIG: path.join(testRoot, "config.yaml"),
        MASTER_DB_PATH: path.join(testRoot, "master.db"),
        MASTER_WORKSPACE_ROOT: path.join(testRoot, "workspace"),
        MASTER_LOG_PATH: logPath,
        MASTER_OLLAMA_URL: `http://127.0.0.1:${offlineOllamaPort}`,
        MASTER_ALLOW_FILE_WRITES: "false",
        MASTER_TERMINAL_ENABLED: "false",
        MASTER_SESSION_TOKEN: sessionToken,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const childStarted = new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    child.stdout.on("data", (chunk) => {
      stdout = appendBounded(stdout, chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr = appendBounded(stderr, chunk);
    });
    await childStarted;
    const ready = await waitForReady(
      port,
      child,
      sessionToken,
      Date.now() + options.timeoutSeconds * 1_000,
    );
    const unauthorized = await fetch(
      `http://127.0.0.1:${port}/api/v1/ready`,
      { redirect: "error", signal: AbortSignal.timeout(2_000) },
    );
    if (unauthorized.status !== 401) {
      throw new Error(
        `Backend sidecar accepted an unauthenticated API request (HTTP ${unauthorized.status}).`,
      );
    }
    const schema = await loadSchema(port, sessionToken);
    if (schema?.info?.title !== "Project Master Local API") {
      throw new Error("Backend sidecar returned an unexpected OpenAPI schema.");
    }
    if (schema?.info?.version !== ready.version) {
      throw new Error("Backend readiness and OpenAPI versions do not match.");
    }
    const { response: healthResponse, payload: health } =
      await authenticatedJson(port, "/api/v1/health", sessionToken, 15_000);
    if (
      !healthResponse.ok ||
      health?.service !== "ready" ||
      health?.version !== ready.version ||
      health?.ok !== false ||
      health?.ollama !== "unreachable"
    ) {
      throw new Error("Backend sidecar did not report Ollama-offline health honestly.");
    }
    const { response: modelResponse, payload: models } =
      await authenticatedJson(port, "/api/v1/models/status", sessionToken);
    if (!modelResponse.ok || models?.ollama_reachable !== false) {
      throw new Error("Backend model status did not preserve offline startup.");
    }
    const { response: toolResponse, payload: tools } =
      await authenticatedJson(port, "/api/v1/tools/status", sessionToken);
    if (
      !toolResponse.ok ||
      !Array.isArray(tools?.tools) ||
      tools?.workspace_writes_enabled !== false ||
      tools?.terminal?.enabled !== false ||
      tools?.diagnostics?.calculator?.ok !== true
    ) {
      throw new Error("Backend tool readiness contract failed.");
    }
    const database = await stat(path.join(testRoot, "master.db")).catch(() => null);
    if (!database?.isFile()) {
      throw new Error("Backend sidecar did not create its configured database.");
    }
    if (interruptedSignal) throw new Error(`Interrupted by ${interruptedSignal}.`);
  } catch (error) {
    const log = logPath
      ? await readFile(logPath, "utf8").catch(() => "")
      : "";
    failure = new Error(
      `${redactSecret(error.message, sessionToken)}` +
        `\nstdout:\n${redactSecret(stdout || "(empty)", sessionToken)}` +
        `\nstderr:\n${redactSecret(stderr || "(empty)", sessionToken)}` +
        `\nlog:\n${redactSecret(log || "(empty)", sessionToken)}`,
    );
  } finally {
    let shutdownConfirmed = !child?.pid;
    try {
      if (child?.pid) {
        await stopProcessTree(child);
        if (port) await waitForPortClosed(port);
        shutdownConfirmed = true;
      }
    } catch (error) {
      const cleanupError = new Error(
        `Sidecar cleanup failed: ${redactSecret(error.message, sessionToken)}`,
      );
      failure = failure
        ? new AggregateError([failure, cleanupError], failure.message)
        : cleanupError;
    }
    if (testRoot && shutdownConfirmed) {
      try {
        if (!safeTempDirectory(testRoot)) {
          throw new Error(`Refusing to remove unexpected directory: ${testRoot}`);
        }
        await rm(testRoot, { recursive: true, force: true });
      } catch (error) {
        failure = failure
          ? new AggregateError([failure, error], failure.message)
          : error;
      }
    } else if (testRoot) {
      console.error(
        `Sidecar fixture preserved after incomplete shutdown: ${testRoot}`,
      );
    }
    for (const [signal, handler] of interruptHandlers) {
      process.off(signal, handler);
    }
    if (interruptedSignal && !failure) {
      failure = new Error(`Sidecar smoke test interrupted by ${interruptedSignal}.`);
    }
  }
  if (failure) throw failure;
  console.log(
    `Backend sidecar auth/offline/tool/lifecycle smoke test passed on 127.0.0.1:${port}.`,
  );
  return binary;
}

if (isMain(import.meta.url)) {
  testBackendSidecar().catch((error) => {
    console.error(`Sidecar smoke test failed: ${error.message}`);
    process.exitCode = 1;
  });
}
