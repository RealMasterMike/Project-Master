import {
  chmod,
  copyFile,
  mkdir,
  readFile,
  stat,
  writeFile,
} from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  isMain,
  prependCargoBin,
  probePython,
  repoRoot,
  resolvePython,
  resolveRustc,
  run,
  sidecarPathForTarget,
  validateTargetTriple,
  venvPython,
} from "./lib/platform.mjs";

function parseArguments(argv) {
  const options = {
    python: undefined,
    venv: path.join(
      repoRoot,
      "ProjectMaster-v0.1.0",
      ".venv-packaging",
    ),
    skipInstall: false,
    reuseCurrentEnvironment: false,
  };
  const nextValue = (name, index) => {
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${name} requires a value.`);
    }
    return value;
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--python") {
      options.python = nextValue(argument, index);
      index += 1;
    } else if (argument === "--venv") {
      options.venv = path.resolve(nextValue(argument, index));
      index += 1;
    } else if (argument === "--skip-install") {
      options.skipInstall = true;
    } else if (argument === "--reuse-current-environment") {
      options.reuseCurrentEnvironment = true;
      options.skipInstall = true;
    } else {
      throw new Error(`Unknown sidecar build argument: ${argument}`);
    }
  }
  return options;
}

async function copyWithRetry(source, destination) {
  let lastError;
  for (let attempt = 1; attempt <= 40; attempt += 1) {
    try {
      await copyFile(source, destination);
      if (process.platform !== "win32") {
        await chmod(destination, 0o755);
      }
      return;
    } catch (error) {
      lastError = error;
      if (attempt < 40) {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
  }
  throw lastError;
}

function capture(command, args, env) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    env,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error(
      `${command} failed: ${(result.stderr || result.stdout).trim()}`,
    );
  }
  return result.stdout.trim();
}

export async function buildBackendSidecar(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  const backendRoot = path.join(repoRoot, "ProjectMaster-v0.1.0");
  const selectedPython = resolvePython({ explicit: options.python });
  const python = options.reuseCurrentEnvironment
    ? selectedPython.command
    : venvPython(options.venv);
  const pythonPrefix = options.reuseCurrentEnvironment
    ? selectedPython.prefix
    : [];
  const env = prependCargoBin();
  let buildPython = selectedPython;

  if (!options.reuseCurrentEnvironment) {
    if (!existsSync(python)) {
      await run(
        selectedPython.command,
        [...selectedPython.prefix, "-m", "venv", options.venv],
        { env },
      );
    }
    buildPython = probePython(
      { command: python, prefix: [], label: python },
      env,
    );
    if (!buildPython) {
      throw new Error(
        `Packaging environment ${options.venv} does not contain a supported Python 3.11–3.14 interpreter.`,
      );
    }
  }

  if (!options.skipInstall) {
    await run(
      python,
      [
        ...pythonPrefix,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-e",
        `${backendRoot}[packaging]`,
      ],
      { env },
    );
  }

  const desktopVersion = JSON.parse(
    await readFile(path.join(repoRoot, "package.json"), "utf8"),
  ).version;
  const versionProbe = [
    "from importlib.metadata import version",
    "import project_master",
    "print(version('project-master-ai'))",
    "print(project_master.__version__)",
  ].join("; ");
  const versions = capture(
    python,
    [...pythonPrefix, "-c", versionProbe],
    env,
  ).split(/\r?\n/);
  if (
    versions.length !== 2 ||
    versions[0] !== desktopVersion ||
    versions[1] !== desktopVersion
  ) {
    throw new Error(
      `Release version mismatch: desktop ${desktopVersion}, package ${versions[0] ?? "missing"}, runtime ${versions[1] ?? "missing"}.`,
    );
  }

  const rustc = resolveRustc(env);
  const targetTriple = validateTargetTriple(
    capture(rustc, ["--print", "host-tuple"], env),
  );
  const windowsTarget = targetTriple.includes("-windows-");
  const extension = windowsTarget ? ".exe" : "";
  const buildRoot = path.join(backendRoot, "build", "sidecar");
  const distRoot = path.join(buildRoot, "dist");
  const workRoot = path.join(buildRoot, "work");
  const specRoot = path.join(buildRoot, "spec");
  const binaryRoot = path.join(repoRoot, "src-tauri", "binaries");
  await Promise.all(
    [distRoot, workRoot, specRoot, binaryRoot].map((directory) =>
      mkdir(directory, { recursive: true }),
    ),
  );

  const pyInstallerArguments = [
    ...pythonPrefix,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "project-master-backend",
    "--collect-data",
    "project_master",
    "--add-data",
    `${path.join(
      backendRoot,
      "src",
      "project_master",
      "integrations",
      "voice",
      "chatterbox_worker.py",
    )}${path.delimiter}project_master_worker_data`,
    "--distpath",
    distRoot,
    "--workpath",
    workRoot,
    "--specpath",
    specRoot,
    "--paths",
    path.join(backendRoot, "src"),
  ];
  if (windowsTarget) {
    pyInstallerArguments.push("--noconsole");
  }
  pyInstallerArguments.push(
    path.join(backendRoot, "src", "project_master", "sidecar.py"),
  );
  await run(python, pyInstallerArguments, { env });

  const builtBinary = path.join(
    distRoot,
    `project-master-backend${extension}`,
  );
  const destination = sidecarPathForTarget(repoRoot, targetTriple);
  const builtStats = await stat(builtBinary).catch(() => null);
  if (!builtStats?.isFile() || builtStats.size === 0) {
    throw new Error(
      `PyInstaller completed without producing a non-empty binary at ${builtBinary}.`,
    );
  }
  await copyWithRetry(builtBinary, destination);
  await writeFile(
    path.join(buildRoot, "sidecar-build.json"),
    `${JSON.stringify(
      {
        schema_version: 1,
        version: desktopVersion,
        target_triple: targetTriple,
        source_binary: path.relative(repoRoot, builtBinary),
        tauri_binary: path.relative(repoRoot, destination),
        size_bytes: builtStats.size,
        python: `${buildPython.version.major}.${buildPython.version.minor}.${buildPython.version.patch}`,
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  console.log(destination);
  return destination;
}

if (isMain(import.meta.url)) {
  buildBackendSidecar().catch((error) => {
    console.error(`Sidecar build failed: ${error.message}`);
    process.exitCode = 1;
  });
}
