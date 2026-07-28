import path from "node:path";

import {
  isMain,
  prependCargoBin,
  repoRoot,
  resolveRustc,
  run,
} from "./lib/platform.mjs";

function parseArguments(argv) {
  if (!argv.length) {
    return { command: null, tauriArgs: [] };
  }
  const [command, ...rest] = argv;
  const tauriArgs = [command, ...rest];
  if (command === "build" && process.platform === "linux") {
    const hasBundles = rest.some(
      (argument) => argument === "--bundles" || argument.startsWith("--bundles="),
    );
    if (!hasBundles) {
      tauriArgs.push("--bundles", "rpm,appimage");
    }
  }
  return { command, tauriArgs };
}

export async function runTauri(argv = process.argv.slice(2)) {
  const { command, tauriArgs } = parseArguments(argv);
  const env = prependCargoBin();
  resolveRustc(env);
  if (command === "dev" || command === "build") {
    await run(
      process.execPath,
      [path.join(repoRoot, "scripts", "build-backend-sidecar.mjs")],
      { env },
    );
  }
  const tauriCli = path.join(
    repoRoot,
    "node_modules",
    "@tauri-apps",
    "cli",
    "tauri.js",
  );
  await run(process.execPath, [tauriCli, ...tauriArgs], { env });
}

if (isMain(import.meta.url)) {
  runTauri().catch((error) => {
    console.error(`Tauri command failed: ${error.message}`);
    process.exitCode = 1;
  });
}

