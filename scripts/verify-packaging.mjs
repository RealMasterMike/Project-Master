import { createHash } from "node:crypto";
import {
  access,
  mkdir,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  isMain,
  prependCargoBin,
  repoRoot,
  resolvePython,
  resolveRustc,
  sidecarPathForTarget,
  validateTargetTriple,
} from "./lib/platform.mjs";
import { brandedAppImageName } from "./build-linux-local.mjs";

function sectionVersion(document, section) {
  const escaped = section.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(
    `^\\[${escaped}\\][\\s\\S]*?^version\\s*=\\s*["']([^"']+)["']`,
    "m",
  ).exec(document);
  if (!match) {
    throw new Error(`Unable to read version from [${section}].`);
  }
  return match[1];
}

function runtimeVersion(document) {
  const match = /^__version__\s*=\s*["']([^"']+)["']/m.exec(document);
  if (!match) {
    throw new Error("Unable to read project_master.__version__.");
  }
  return match[1];
}

function capture(command, args, env) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    env,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0) {
    return null;
  }
  return result.stdout.trim();
}

async function sha256(filePath) {
  const content = await readFile(filePath);
  return createHash("sha256").update(content).digest("hex");
}

async function collectFiles(directory, predicate) {
  const found = [];
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...(await collectFiles(candidate, predicate)));
    } else if (entry.isFile() && predicate(candidate)) {
      found.push(candidate);
    }
  }
  return found;
}

async function verifyVersionsAndConfig() {
  const [
    packageDocument,
    packageLockDocument,
    tauriDocument,
    appImageDocument,
    appImageDesktop,
    cargoDocument,
    pyprojectDocument,
    runtimeDocument,
  ] = await Promise.all([
    readFile(path.join(repoRoot, "package.json"), "utf8").then(JSON.parse),
    readFile(path.join(repoRoot, "package-lock.json"), "utf8").then(JSON.parse),
    readFile(path.join(repoRoot, "src-tauri", "tauri.conf.json"), "utf8").then(
      JSON.parse,
    ),
    readFile(
      path.join(repoRoot, "scripts", "tauri.appimage.conf.json"),
      "utf8",
    ).then(JSON.parse),
    readFile(
      path.join(
        repoRoot,
        "src-tauri",
        "linux",
        "project-master-appimage.desktop",
      ),
      "utf8",
    ),
    readFile(path.join(repoRoot, "src-tauri", "Cargo.toml"), "utf8"),
    readFile(
      path.join(repoRoot, "ProjectMaster-v0.1.0", "pyproject.toml"),
      "utf8",
    ),
    readFile(
      path.join(
        repoRoot,
        "ProjectMaster-v0.1.0",
        "src",
        "project_master",
        "__init__.py",
      ),
      "utf8",
    ),
  ]);
  const expected = packageDocument.version;
  const versions = {
    package: expected,
    package_lock: packageLockDocument.version,
    package_lock_root: packageLockDocument.packages?.[""]?.version,
    tauri: tauriDocument.version,
    cargo: sectionVersion(cargoDocument, "package"),
    python_package: sectionVersion(pyprojectDocument, "project"),
    python_runtime: runtimeVersion(runtimeDocument),
  };
  const mismatches = Object.entries(versions).filter(
    ([, version]) => version !== expected,
  );
  if (mismatches.length) {
    throw new Error(
      `Version mismatch: ${mismatches
        .map(([name, version]) => `${name}=${version ?? "missing"}`)
        .join(", ")}; expected ${expected}.`,
    );
  }
  const requiredScripts = {
    "backend:sidecar": "node scripts/build-backend-sidecar.mjs",
    "backend:sidecar:test": "node scripts/test-backend-sidecar.mjs",
    "tauri:dev": "node scripts/run-tauri.mjs dev",
  };
  for (const [name, command] of Object.entries(requiredScripts)) {
    if (packageDocument.scripts?.[name] !== command) {
      throw new Error(
        `package.json script ${JSON.stringify(name)} is not cross-platform.`,
      );
    }
  }
  if (
    tauriDocument.bundle?.externalBin?.[0] !==
    "binaries/project-master-backend"
  ) {
    throw new Error("Tauri externalBin does not point to the backend sidecar.");
  }
  if (
    !tauriDocument.bundle?.linux?.rpm ||
    !tauriDocument.bundle?.linux?.appimage
  ) {
    throw new Error("Tauri Linux RPM and AppImage metadata is incomplete.");
  }
  if (
    appImageDocument.productName !== "master" ||
    appImageDocument.bundle?.createUpdaterArtifacts !== false ||
    appImageDocument.bundle?.linux?.appimage?.files?.[
      "/usr/share/applications/master.desktop"
    ] !== "linux/project-master-appimage.desktop"
  ) {
    throw new Error(
      "Fedora AppImage staging must align the master executable, icon, and desktop file.",
    );
  }
  for (const line of [
    "Name=Project Master",
    "Exec=master",
    "Icon=master",
  ]) {
    if (!appImageDesktop.split(/\r?\n/).includes(line)) {
      throw new Error(`AppImage desktop metadata is missing ${line}.`);
    }
  }
  return expected;
}

function preflight() {
  const env = prependCargoBin();
  const checks = [];
  const add = (name, ok, detail) => checks.push({ name, ok, detail });
  try {
    const python = resolvePython({ env });
    add(
      "Python",
      true,
      `${python.label} ${python.version.major}.${python.version.minor}.${python.version.patch}`,
    );
  } catch (error) {
    add("Python", false, error.message);
  }
  try {
    const rustc = resolveRustc(env);
    add("Rust", true, capture(rustc, ["--version"], env) ?? rustc);
  } catch (error) {
    add("Rust", false, error.message);
  }
  add("Node", Number(process.versions.node.split(".")[0]) >= 20, process.version);
  add(
    "Tauri CLI",
    Boolean(
      capture(
        process.execPath,
        [
          path.join(
            repoRoot,
            "node_modules",
            "@tauri-apps",
            "cli",
            "tauri.js",
          ),
          "--version",
        ],
        env,
      ),
    ),
    "node_modules/@tauri-apps/cli",
  );
  if (process.platform === "linux") {
    const packages = [
      "webkit2gtk4.1-devel",
      "gtk3-devel",
      "libappindicator-gtk3-devel",
      "librsvg2-devel",
      "dbus-devel",
      "pkgconf-pkg-config",
      "openssl-devel",
      "patchelf",
      "rpm-build",
      "fuse",
      "fuse-libs",
    ];
    for (const packageName of packages) {
      const result = spawnSync("rpm", ["-q", packageName], {
        encoding: "utf8",
        windowsHide: true,
      });
      add(
        `RPM ${packageName}`,
        result.status === 0,
        (result.stdout || result.stderr).trim(),
      );
    }
  }
  return checks;
}

export async function verifyPackaging(argv = process.argv.slice(2)) {
  const options = new Set(argv);
  const known = new Set(["--preflight", "--sidecar", "--artifacts"]);
  for (const option of options) {
    if (!known.has(option)) {
      throw new Error(`Unknown packaging verification argument: ${option}`);
    }
  }
  const version = await verifyVersionsAndConfig();
  console.log(`Packaging configuration is version-aligned at ${version}.`);

  if (options.has("--preflight")) {
    const checks = preflight();
    for (const check of checks) {
      console.log(`${check.ok ? "PASS" : "FAIL"}  ${check.name}: ${check.detail}`);
    }
    const failed = checks.filter((check) => !check.ok);
    if (failed.length) {
      throw new Error(
        `${failed.length} packaging prerequisite(s) are missing.`,
      );
    }
  }

  if (options.has("--sidecar")) {
    const env = prependCargoBin();
    const rustc = resolveRustc(env);
    const target = validateTargetTriple(
      capture(rustc, ["--print", "host-tuple"], env) ?? "",
    );
    const sidecar = sidecarPathForTarget(repoRoot, target);
    await access(sidecar);
    const sidecarStats = await stat(sidecar);
    if (!sidecarStats.isFile() || sidecarStats.size === 0) {
      throw new Error(`Sidecar is missing or empty: ${sidecar}`);
    }
    console.log(
      `PASS  Sidecar ${path.relative(repoRoot, sidecar)} (${sidecarStats.size} bytes, sha256 ${await sha256(sidecar)})`,
    );
  }

  if (options.has("--artifacts")) {
    const bundleRoot = path.join(
      repoRoot,
      "src-tauri",
      "target",
      "release",
      "bundle",
    );
    const requiredBundles =
      process.platform === "linux"
        ? [
            { directory: "rpm", suffix: ".rpm", label: "RPM" },
            {
              directory: "appimage",
              suffix: ".AppImage",
              label: "AppImage",
            },
          ]
        : process.platform === "win32"
          ? [
              { directory: "msi", suffix: ".msi", label: "MSI" },
              { directory: "nsis", suffix: ".exe", label: "NSIS" },
            ]
          : null;
    if (!requiredBundles) {
      throw new Error(
        `Artifact verification is not configured for ${process.platform}.`,
      );
    }
    const artifacts = [];
    for (const bundle of requiredBundles) {
      const matches = await collectFiles(
        path.join(bundleRoot, bundle.directory),
        (filePath) =>
          filePath.endsWith(bundle.suffix) &&
          path.basename(filePath).includes(version),
      );
      if (!matches.length) {
        throw new Error(
          `No ${bundle.label} artifact for version ${version} found below ${path.join(bundleRoot, bundle.directory)}.`,
        );
      }
      if (matches.length !== 1) {
        throw new Error(
          `Expected exactly one ${bundle.label} artifact for version ${version}; found ${matches.length}.`,
        );
      }
      if (
        process.platform === "linux" &&
        bundle.label === "AppImage" &&
        path.basename(matches[0]) !== brandedAppImageName(version)
      ) {
        throw new Error(
          `Linux AppImage must use the branded filename ${brandedAppImageName(version)}.`,
        );
      }
      artifacts.push(...matches);
    }
    const lines = [];
    for (const artifact of artifacts.sort()) {
      const artifactStats = await stat(artifact);
      if (artifactStats.size === 0) {
        throw new Error(`Release artifact is empty: ${artifact}`);
      }
      lines.push(`${await sha256(artifact)}  ${path.relative(repoRoot, artifact)}`);
    }
    const outputRoot = path.join(repoRoot, "release", "local");
    await mkdir(outputRoot, { recursive: true });
    const checksumPath = path.join(
      outputRoot,
      `Project-Master-${version}-${os.platform()}-${os.arch()}-SHA256SUMS.txt`,
    );
    await writeFile(checksumPath, `${lines.join("\n")}\n`, "utf8");
    console.log(`Verified ${lines.length} artifact(s); wrote ${checksumPath}.`);
  }
}

if (isMain(import.meta.url)) {
  verifyPackaging().catch((error) => {
    console.error(`Packaging verification failed: ${error.message}`);
    process.exitCode = 1;
  });
}
