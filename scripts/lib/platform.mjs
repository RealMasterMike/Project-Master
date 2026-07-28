import { spawn, spawnSync } from "node:child_process";
import { accessSync, constants, existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

export function executableName(name, platform = process.platform) {
  return platform === "win32" ? `${name}.exe` : name;
}

export function venvPython(venvRoot, platform = process.platform) {
  const platformPath = platform === "win32" ? path.win32 : path.posix;
  return platform === "win32"
    ? platformPath.join(venvRoot, "Scripts", "python.exe")
    : platformPath.join(venvRoot, "bin", "python");
}

export function prependCargoBin(env = process.env, platform = process.platform) {
  const cargoBin = path.join(os.homedir(), ".cargo", "bin");
  if (!existsSync(cargoBin)) {
    return { ...env };
  }
  const separator = platform === "win32" ? ";" : ":";
  const pathKeys = Object.keys(env).filter(
    (key) => key.toLowerCase() === "path",
  );
  const pathKey = pathKeys[0] ?? "PATH";
  const currentPath = env[pathKey] ?? "";
  const entries = currentPath.split(separator).filter(Boolean);
  if (!entries.some((entry) => path.resolve(entry) === path.resolve(cargoBin))) {
    entries.unshift(cargoBin);
  }
  const result = { ...env };
  for (const duplicate of pathKeys.slice(1)) {
    delete result[duplicate];
  }
  result[pathKey] = entries.join(separator);
  return result;
}

export function resolveRustc(env = process.env, platform = process.platform) {
  const candidates = [
    env.RUSTC,
    path.join(os.homedir(), ".cargo", "bin", executableName("rustc", platform)),
    executableName("rustc", platform),
  ].filter(Boolean);
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ["--version"], {
      env: prependCargoBin(env, platform),
      encoding: "utf8",
      windowsHide: true,
    });
    if (result.status === 0) {
      return candidate;
    }
  }
  throw new Error(
    "Rust is required. Install rustup, or set RUSTC to the rustc executable path.",
  );
}

export function parsePythonVersion(text) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(text.trim());
  if (!match) {
    throw new Error(`Unexpected Python version response: ${JSON.stringify(text)}`);
  }
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
  };
}

export function isSupportedPackagingPython(version) {
  return (
    version.major === 3 &&
    version.minor >= 11 &&
    version.minor <= 14
  );
}

export function probePython(candidate, env = process.env) {
  const prefix = candidate.prefix ?? [];
  const result = spawnSync(
    candidate.command,
    [
      ...prefix,
      "-c",
      "import sys; print('.'.join(str(value) for value in sys.version_info[:3]))",
    ],
    {
      env,
      encoding: "utf8",
      windowsHide: true,
    },
  );
  if (result.status !== 0) {
    return null;
  }
  try {
    const version = parsePythonVersion(result.stdout);
    return isSupportedPackagingPython(version)
      ? { ...candidate, version }
      : null;
  } catch {
    return null;
  }
}

export function resolvePython({
  explicit,
  env = process.env,
  platform = process.platform,
} = {}) {
  const configured = explicit ?? env.PROJECT_MASTER_PACKAGING_PYTHON;
  if (configured) {
    const result = probePython(
      { command: configured, prefix: [], label: configured },
      env,
    );
    if (result) {
      return result;
    }
    throw new Error(
      `Configured packaging Python is unavailable or unsupported: ${configured}. Python 3.11–3.14 is required.`,
    );
  }
  const candidates = [];
  if (platform === "win32") {
    for (const version of ["-3.12", "-3.11", "-3.13", "-3.14"]) {
      candidates.push({
        command: "py",
        prefix: [version],
        label: `py ${version}`,
      });
    }
    candidates.push({ command: "python", prefix: [], label: "python" });
  } else {
    for (const command of [
      "python3.12",
      "python3.11",
      "python3.13",
      "python3.14",
      "python3",
      "python",
    ]) {
      candidates.push({ command, prefix: [], label: command });
    }
  }
  for (const candidate of candidates) {
    const result = probePython(candidate, env);
    if (result) {
      return result;
    }
  }
  throw new Error(
    "Python 3.11–3.14 is required. Set PROJECT_MASTER_PACKAGING_PYTHON to a compatible executable.",
  );
}

export function validateTargetTriple(value) {
  const triple = value.trim();
  if (
    !/^[A-Za-z0-9_][A-Za-z0-9_.-]{4,127}$/.test(triple) ||
    !triple.includes("-")
  ) {
    throw new Error(`Rust returned an unsafe target triple: ${JSON.stringify(value)}`);
  }
  return triple;
}

export function sidecarPathForTarget(
  root,
  targetTriple,
  platform = process.platform,
) {
  const extension = platform === "win32" || targetTriple.includes("-windows-")
    ? ".exe"
    : "";
  return path.join(
    root,
    "src-tauri",
    "binaries",
    `project-master-backend-${targetTriple}${extension}`,
  );
}

export function ensureExecutable(filePath) {
  accessSync(filePath, constants.R_OK);
  if (process.platform !== "win32") {
    accessSync(filePath, constants.X_OK);
  }
}

export function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? repoRoot,
      env: options.env ?? process.env,
      stdio: options.stdio ?? "inherit",
      windowsHide: true,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(
        new Error(
          `${command} exited with ${code ?? `signal ${signal ?? "unknown"}`}.`,
        ),
      );
    });
  });
}

export function isMain(importMetaUrl) {
  if (!process.argv[1]) {
    return false;
  }
  return path.resolve(fileURLToPath(importMetaUrl)) === path.resolve(process.argv[1]);
}
