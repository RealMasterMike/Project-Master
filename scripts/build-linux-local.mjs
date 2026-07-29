import {
  chmod,
  copyFile,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  rm,
  stat,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  isMain,
  prependCargoBin,
  repoRoot,
  resolveRustc,
  run,
} from "./lib/platform.mjs";

export const APPIMAGE_PRODUCT_NAME = "master";
export const APPIMAGE_DESKTOP_FILE = "master.desktop";
export const APPIMAGE_ICON_FILE = "master.png";
export const BRANDED_APPIMAGE_PREFIX = "Project-Master";
export const BUNDLE_MARKERS = {
  pristine: "__TAURI_BUNDLE_TYPE_VAR_UNK",
  rpm: "__TAURI_BUNDLE_TYPE_VAR_RPM",
  appImage: "__TAURI_BUNDLE_TYPE_VAR_APP",
};
const KNOWN_APPIMAGE_OUTPUT =
  /^(?:master|Project_Master|Project-Master)[A-Za-z0-9._+-]*\.AppImage$/;

export function linuxArtifactArchitecture(architecture = os.arch()) {
  const supported = {
    x64: "x86_64",
    arm64: "aarch64",
    arm: "armhf",
  };
  const artifactArchitecture = supported[architecture];
  if (!artifactArchitecture) {
    throw new Error(
      `Unsupported Linux artifact architecture: ${architecture}.`,
    );
  }
  return artifactArchitecture;
}

export function linuxElfClass(architecture = os.arch()) {
  // Validate through the public artifact mapping so both helpers accept the
  // same set of local Linux architectures.
  linuxArtifactArchitecture(architecture);
  return architecture === "arm" ? 1 : 2;
}

export function brandedAppImageName(version, architecture = os.arch()) {
  if (!/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(version)) {
    throw new Error(`Unsafe AppImage version: ${JSON.stringify(version)}.`);
  }
  return `${BRANDED_APPIMAGE_PREFIX}-${version}-${linuxArtifactArchitecture(architecture)}.AppImage`;
}

function desktopFields(document) {
  const fields = new Map();
  for (const rawLine of document.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith("[")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    fields.set(line.slice(0, separator), line.slice(separator + 1));
  }
  return fields;
}

async function requireNonEmptyFile(filePath, label) {
  const fileStats = await stat(filePath).catch(() => null);
  if (!fileStats?.isFile() || fileStats.size === 0) {
    throw new Error(`${label} is missing or empty: ${filePath}`);
  }
  return fileStats;
}

export async function verifyBundleMarker(
  filePath,
  expectedMarker,
  label = "Tauri executable",
) {
  const content = await readFile(filePath);
  if (!content.includes(Buffer.from(expectedMarker))) {
    throw new Error(
      `${label} does not contain the expected ${expectedMarker} bundle marker.`,
    );
  }
}

export async function verifyStagedAppDir(appDir) {
  const appDirStats = await stat(appDir).catch(() => null);
  if (!appDirStats?.isDirectory()) {
    throw new Error(`Tauri did not stage the expected AppDir: ${appDir}`);
  }

  const desktopPath = path.join(appDir, APPIMAGE_DESKTOP_FILE);
  const desktopStats = await lstat(desktopPath).catch(() => null);
  if (!desktopStats || (!desktopStats.isFile() && !desktopStats.isSymbolicLink())) {
    throw new Error(`AppImage desktop entry is missing: ${desktopPath}`);
  }
  const fields = desktopFields(await readFile(desktopPath, "utf8"));
  const expectedFields = {
    Name: "Project Master",
    Exec: APPIMAGE_PRODUCT_NAME,
    Icon: APPIMAGE_PRODUCT_NAME,
    Type: "Application",
    Terminal: "false",
  };
  for (const [name, expected] of Object.entries(expectedFields)) {
    if (fields.get(name) !== expected) {
      throw new Error(
        `AppImage desktop field ${name} must be ${JSON.stringify(expected)}; received ${JSON.stringify(fields.get(name))}.`,
      );
    }
  }

  await Promise.all([
    requireNonEmptyFile(
      path.join(appDir, APPIMAGE_ICON_FILE),
      "AppImage root icon",
    ),
    requireNonEmptyFile(
      path.join(appDir, "usr", "bin", APPIMAGE_PRODUCT_NAME),
      "AppImage desktop executable",
    ),
    requireNonEmptyFile(
      path.join(appDir, "usr", "bin", "project-master-backend"),
      "AppImage backend sidecar",
    ),
    requireNonEmptyFile(path.join(appDir, "AppRun"), "AppImage launcher"),
    requireNonEmptyFile(
      path.join(appDir, "usr", "lib", "libwebkit2gtk-4.1.so.0"),
      "AppImage WebKit runtime",
    ),
    requireNonEmptyFile(
      path.join(
        appDir,
        "usr",
        "libexec",
        "webkit2gtk-4.1",
        "WebKitWebProcess",
      ),
      "AppImage WebKit subprocess",
    ),
    requireNonEmptyFile(
      path.join(appDir, "usr", "lib", "libgstreamer-1.0.so.0"),
      "AppImage media runtime",
    ),
    verifyBundleMarker(
      path.join(appDir, "usr", "bin", APPIMAGE_PRODUCT_NAME),
      BUNDLE_MARKERS.appImage,
      "Staged AppImage executable",
    ),
  ]);
}

/**
 * Drop staged GStreamer plugins that do not match the AppImage architecture.
 *
 * The bundler can pick i386 plugins from a multilib host and stage them beside
 * a 64-bit libgstreamer. WebKit then finds a plugin directory containing only
 * unloadable objects, cannot construct `appsink`/`autoaudiosink`, and its web
 * process crashes the first time audio plays. An empty directory is safer: the
 * host's matching plugin registry is used instead.
 */
export async function removeForeignArchitectureGstPlugins(
  appDir,
  architecture = os.arch(),
) {
  const pluginDir = path.join(appDir, "usr", "lib", "gstreamer-1.0");
  const expectedClass = linuxElfClass(architecture);
  let entries;
  try {
    entries = await readdir(pluginDir, { withFileTypes: true });
  } catch {
    return [];
  }
  const removed = [];
  for (const entry of entries) {
    if (
      (!entry.isFile() && !entry.isSymbolicLink()) ||
      !entry.name.endsWith(".so")
    ) {
      continue;
    }
    const pluginPath = path.join(pluginDir, entry.name);
    if ((await readElfClass(pluginPath)) === expectedClass) continue;
    await rm(pluginPath, { force: true });
    removed.push(entry.name);
  }
  return removed;
}

/**
 * Module directories that a 64-bit runtime dlopen()s by scanning, beyond GStreamer's.
 *
 * These share the GStreamer failure mode: the loader finds a populated directory, every
 * object in it is unloadable, and the feature silently dies instead of falling back to the
 * host. `gio/modules` shipped a lone i386 `libgiognutls.so` in 0.4.0 — that is GIO's TLS
 * backend, so leaving it risks breaking HTTPS rather than merely wasting space.
 */
const FOREIGN_MODULE_DIRS = [path.join("usr", "lib", "gio", "modules")];

/**
 * Drop staged loadable modules that do not match the AppImage architecture.
 *
 * Same reasoning as the GStreamer plugin sweep: an empty directory is safer than one
 * holding only foreign objects, because the host's matching modules are then used.
 */
export async function removeForeignArchitectureModules(
  appDir,
  architecture = os.arch(),
) {
  const expectedClass = linuxElfClass(architecture);
  const removed = [];
  for (const relative of FOREIGN_MODULE_DIRS) {
    const dir = path.join(appDir, relative);
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (
        (!entry.isFile() && !entry.isSymbolicLink()) ||
        !entry.name.endsWith(".so")
      ) {
        continue;
      }
      const modulePath = path.join(dir, entry.name);
      if ((await readElfClass(modulePath)) === expectedClass) continue;
      await rm(modulePath, { force: true });
      removed.push(path.join(relative, entry.name));
    }
  }
  return removed;
}

/**
 * Drop staged library directories whose objects do not match the AppImage architecture.
 *
 * Same root cause as the plugin sweep above: on a multilib host the bundler also stages
 * whole `lib32`/`lib64` trees. Those libraries are never loadable by the packaged binary
 * and nothing puts them on the library path, so they are pure weight — the 0.4.0 AppImage
 * carried 18 MB of i386 objects including a second `libgstreamer-1.0.so.0`. Removing them
 * also stops a future loader-path change from resurrecting the crash the plugin sweep
 * fixed.
 *
 * A directory is only removed when it is non-empty and *every* ELF object in it is
 * foreign, so a correctly-staged directory can never be deleted by this.
 */
export async function removeForeignArchitectureLibDirs(
  appDir,
  architecture = os.arch(),
) {
  const expectedClass = linuxElfClass(architecture);
  const removed = [];
  for (const candidate of ["lib32", "lib64", "libx32"]) {
    const dir = path.join(appDir, "usr", candidate);
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    let sawElf = false;
    let allForeign = true;
    for (const entry of entries) {
      if (!entry.isFile() && !entry.isSymbolicLink()) continue;
      const elfClass = await readElfClass(path.join(dir, entry.name));
      if (elfClass === null) continue;
      sawElf = true;
      if (elfClass === expectedClass) {
        allForeign = false;
        break;
      }
    }
    if (!sawElf || !allForeign) continue;
    await rm(dir, { recursive: true, force: true });
    removed.push(candidate);
  }
  return removed;
}

/** Read the ELF class byte (index 4): 2 is 64-bit, 1 is 32-bit. */
export async function readElfClass(filePath) {
  const handle = await open(filePath, "r");
  try {
    const header = Buffer.alloc(5);
    const { bytesRead } = await handle.read(header, 0, 5, 0);
    if (bytesRead < 5) return null;
    const isElf =
      header[0] === 0x7f &&
      header[1] === 0x45 &&
      header[2] === 0x4c &&
      header[3] === 0x46;
    return isElf && (header[4] === 1 || header[4] === 2)
      ? header[4]
      : null;
  } finally {
    await handle.close();
  }
}

export async function finalizeLinuxAppImage({
  bundleRoot,
  version,
  architecture = os.arch(),
}) {
  const appDir = path.join(
    bundleRoot,
    `${APPIMAGE_PRODUCT_NAME}.AppDir`,
  );
  await verifyStagedAppDir(appDir);

  const entries = await readdir(bundleRoot, { withFileTypes: true });
  const images = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".AppImage"))
    .map((entry) => entry.name);
  const destination = path.join(
    bundleRoot,
    brandedAppImageName(version, architecture),
  );
  const generated = images.filter(
    (name) => path.join(bundleRoot, name) !== destination,
  );
  const unknown = generated.filter(
    (name) => !KNOWN_APPIMAGE_OUTPUT.test(name),
  );
  if (unknown.length) {
    throw new Error(
      `Refusing to replace unexpected AppImage output: ${unknown.join(", ")}.`,
    );
  }
  if (generated.length > 1 || (generated.length === 0 && !images.length)) {
    throw new Error(
      `Expected one generated AppImage or one existing branded final in ${bundleRoot}; found ${images.length}.`,
    );
  }
  const source =
    generated.length === 1
      ? path.join(bundleRoot, generated[0])
      : destination;
  await requireNonEmptyFile(source, "Generated AppImage");
  if (source !== destination) {
    await rm(destination, { force: true });
    await rename(source, destination);
  }
  await chmod(destination, 0o755);
  await requireNonEmptyFile(destination, "Branded AppImage");
  return destination;
}

/**
 * Produce the final AppImage only after its staged AppDir is architecture-clean.
 *
 * Tauri's bundle command can already have emitted an AppImage by the time the
 * caller regains control. That artifact is discarded and the output plugin is
 * run again through `produceArtifact`, guaranteeing that the retained image was
 * created from the cleaned tree rather than merely cleaning the tree afterward.
 */
export async function createLinuxAppImageFromStagedDir({
  bundleRoot,
  version,
  architecture = os.arch(),
  produceArtifact,
}) {
  if (typeof produceArtifact !== "function") {
    throw new TypeError("An AppImage artifact producer is required.");
  }
  const appDir = path.join(
    bundleRoot,
    `${APPIMAGE_PRODUCT_NAME}.AppDir`,
  );
  await verifyStagedAppDir(appDir);
  await removeForeignArchitectureGstPlugins(appDir, architecture);
  await removeForeignArchitectureModules(appDir, architecture);
  await removeForeignArchitectureLibDirs(appDir, architecture);
  await removeKnownAppImageOutputs(bundleRoot);
  await produceArtifact(appDir);
  return finalizeLinuxAppImage({ bundleRoot, version, architecture });
}

export function tauriCacheDirectory(
  env = process.env,
  homeDirectory = os.homedir(),
) {
  if (env.PROJECT_MASTER_TAURI_CACHE_DIR) {
    return path.resolve(env.PROJECT_MASTER_TAURI_CACHE_DIR);
  }
  const cacheHome = env.XDG_CACHE_HOME
    ? path.resolve(env.XDG_CACHE_HOME)
    : path.join(homeDirectory, ".cache");
  return path.join(cacheHome, "tauri");
}

export function appImageOutputPluginPath(
  env = process.env,
  homeDirectory = os.homedir(),
) {
  return path.join(
    tauriCacheDirectory(env, homeDirectory),
    "linuxdeploy-plugin-appimage.AppImage",
  );
}

export function localLinuxBuildCommands(root = repoRoot) {
  const tauriCli = path.join(
    root,
    "node_modules",
    "@tauri-apps",
    "cli",
    "tauri.js",
  );
  return {
    clean: {
      command: "cargo",
      args: [
        "clean",
        "--release",
        "--package",
        "master",
        "--manifest-path",
        path.join(root, "src-tauri", "Cargo.toml"),
      ],
    },
    build: {
      command: process.execPath,
      args: [
        path.join(root, "scripts", "run-tauri.mjs"),
        "build",
        "--no-bundle",
        "--config",
        "scripts/tauri.local.conf.json",
      ],
    },
    rpm: {
      command: process.execPath,
      args: [
        tauriCli,
        "bundle",
        "--bundles",
        "rpm",
        "--config",
        "scripts/tauri.local.conf.json",
      ],
    },
    appImage: {
      command: process.execPath,
      args: [
        tauriCli,
        "bundle",
        "--bundles",
        "appimage",
        "--config",
        "scripts/tauri.appimage.conf.json",
      ],
    },
    verify: {
      command: process.execPath,
      args: [
        path.join(root, "scripts", "verify-packaging.mjs"),
        "--sidecar",
        "--artifacts",
      ],
    },
  };
}

async function removeKnownAppImageOutputs(bundleRoot) {
  const entries = await readdir(bundleRoot, { withFileTypes: true }).catch(
    () => [],
  );
  await Promise.all(
    entries
      .filter(
        (entry) =>
          entry.isFile() &&
          entry.name.endsWith(".AppImage") &&
          KNOWN_APPIMAGE_OUTPUT.test(entry.name),
      )
      .map((entry) => rm(path.join(bundleRoot, entry.name), { force: true })),
  );
}

export async function buildLocalLinux() {
  if (process.platform !== "linux") {
    throw new Error("The local Linux package build must run on Linux.");
  }
  const packageDocument = JSON.parse(
    await readFile(path.join(repoRoot, "package.json"), "utf8"),
  );
  const version = packageDocument.version;
  const appImageBundleRoot = path.join(
    repoRoot,
    "src-tauri",
    "target",
    "release",
    "bundle",
    "appimage",
  );
  const commands = localLinuxBuildCommands();
  const env = prependCargoBin();
  resolveRustc(env);
  const releaseBinary = path.join(
    repoRoot,
    "src-tauri",
    "target",
    "release",
    APPIMAGE_PRODUCT_NAME,
  );
  const pristineRoot = path.join(
    repoRoot,
    "src-tauri",
    "target",
    "release",
    "project-master-package-staging",
  );
  const pristineBinary = path.join(pristineRoot, APPIMAGE_PRODUCT_NAME);

  // Bundle marker patching mutates Cargo's release artifact. Clean only the
  // application package so one subsequent build relinks a pristine marker
  // without recompiling the dependency graph.
  await run(commands.clean.command, commands.clean.args, { env });

  // run-tauri owns the only sidecar, frontend, and Rust compilation.
  await run(commands.build.command, commands.build.args, { env });
  await verifyBundleMarker(
    releaseBinary,
    BUNDLE_MARKERS.pristine,
    "Fresh no-bundle executable",
  );
  await rm(pristineRoot, { recursive: true, force: true });
  await mkdir(pristineRoot, { recursive: true });
  await copyFile(releaseBinary, pristineBinary);
  const pristineMode = (await stat(releaseBinary)).mode & 0o777;

  try {
    // Tauri patches a bundle-specific copy and restores the release binary
    // after a successful bundle. Keep our pristine copy as a failure-safe.
    await run(commands.rpm.command, commands.rpm.args, { env });
    await verifyBundleMarker(
      releaseBinary,
      BUNDLE_MARKERS.pristine,
      "Post-RPM release executable",
    );

    // AppImage bundling reuses the same compiled binary. Fedora 44 emits RELR
    // sections that the old linuxdeploy strip bundled by Tauri cannot parse.
    await mkdir(appImageBundleRoot, { recursive: true });
    await rm(
      path.join(appImageBundleRoot, `${APPIMAGE_PRODUCT_NAME}.AppDir`),
      { recursive: true, force: true },
    );
    await removeKnownAppImageOutputs(appImageBundleRoot);
    let tauriBundleError;
    try {
      await run(commands.appImage.command, commands.appImage.args, {
        env: { ...env, NO_STRIP: "1" },
      });
    } catch (error) {
      tauriBundleError = error;
    }
    const stagedAppDir = path.join(
      appImageBundleRoot,
      `${APPIMAGE_PRODUCT_NAME}.AppDir`,
    );
    if (tauriBundleError) {
      // A known RELR-only linuxdeploy exit can occur after Tauri has fully
      // staged and marked the AppDir. Validate that state before bypassing only
      // the obsolete deploy/strip phase.
      try {
        await verifyStagedAppDir(stagedAppDir);
      } catch (stagingError) {
        throw new AggregateError(
          [tauriBundleError, stagingError],
          "Tauri AppImage bundling failed before a valid AppDir was staged.",
        );
      }
    }
    const architecture = os.arch();
    const outputPlugin = appImageOutputPluginPath(env);
    await requireNonEmptyFile(
      outputPlugin,
      "Tauri cached AppImage output plugin",
    );
    await chmod(outputPlugin, 0o755);
    // Even when Tauri's first bundle succeeds, discard that image and invoke
    // the output plugin again after removing foreign plugins from the AppDir.
    // Otherwise the staged tree is clean but the already-created artifact is
    // still contaminated.
    const appImage = await createLinuxAppImageFromStagedDir({
      bundleRoot: appImageBundleRoot,
      version,
      architecture,
      produceArtifact: async (cleanAppDir) => {
        await run(
          outputPlugin,
          ["--appimage-extract-and-run", "--appdir", cleanAppDir],
          {
            cwd: appImageBundleRoot,
            env: {
              ...env,
              ARCH: linuxArtifactArchitecture(architecture),
            },
          },
        );
      },
    });
    console.log(`Finalized ${path.relative(repoRoot, appImage)}.`);

    await run(commands.verify.command, commands.verify.args, { env });
    return appImage;
  } finally {
    // A failed bundler may leave its marker in Cargo's output. Always restore
    // the pristine executable so a retry starts from a known state.
    await copyFile(pristineBinary, releaseBinary);
    await chmod(releaseBinary, pristineMode);
    await rm(pristineRoot, { recursive: true, force: true });
  }
}

if (isMain(import.meta.url)) {
  buildLocalLinux().catch((error) => {
    console.error(`Local Linux build failed: ${error.message}`);
    process.exitCode = 1;
  });
}
