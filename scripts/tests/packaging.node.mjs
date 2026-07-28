import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import {
  access,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  appImageOutputPluginPath,
  BUNDLE_MARKERS,
  brandedAppImageName,
  finalizeLinuxAppImage,
  linuxArtifactArchitecture,
  localLinuxBuildCommands,
  tauriCacheDirectory,
  verifyBundleMarker,
} from "../build-linux-local.mjs";
import {
  buildBackendEnvironment,
  catalogModelSupportsCompletion,
  catalogModelSupportsTools,
  parseAcceptanceArguments,
  prepareChatterboxClone,
  prepareIsolatedProfile,
  renderAcceptanceMarkdown,
  safeAcceptanceRoot,
  sanitizeReportValue,
  selectToolModel,
  unloadHarnessOllamaModels,
  validateCatalogPartition,
  validateVoiceArtifact,
  writeReports,
} from "../test-linux-acceptance.mjs";
import {
  processGroupIsRunning,
  stopProcessTree,
  waitForExit,
} from "../test-backend-sidecar.mjs";
import {
  isSupportedPackagingPython,
  parsePythonVersion,
  repoRoot,
  sidecarPathForTarget,
  validateTargetTriple,
  venvPython,
} from "../lib/platform.mjs";
import { verifyPackaging } from "../verify-packaging.mjs";

test("packaging Python version parsing is strict and bounded", () => {
  assert.deepEqual(parsePythonVersion("3.11.15\n"), {
    major: 3,
    minor: 11,
    patch: 15,
  });
  assert.equal(
    isSupportedPackagingPython({ major: 3, minor: 14, patch: 0 }),
    true,
  );
  assert.equal(
    isSupportedPackagingPython({ major: 3, minor: 15, patch: 0 }),
    false,
  );
  assert.throws(() => parsePythonVersion("Python 3.11.15"));
});

test("target triple and sidecar paths cannot inject directories", () => {
  const triple = validateTargetTriple("x86_64-unknown-linux-gnu\n");
  assert.equal(triple, "x86_64-unknown-linux-gnu");
  assert.equal(
    sidecarPathForTarget("/repo", triple, "linux"),
    path.join(
      "/repo",
      "src-tauri",
      "binaries",
      "project-master-backend-x86_64-unknown-linux-gnu",
    ),
  );
  assert.throws(() => validateTargetTriple("../../tmp/sidecar"));
  assert.throws(() => validateTargetTriple("not-a-triple/with-path"));
});

test("virtual environment Python paths remain platform-specific", () => {
  assert.equal(
    venvPython("/build/venv", "linux"),
    path.join("/build/venv", "bin", "python"),
  );
  assert.equal(
    venvPython("C:\\build\\venv", "win32"),
    path.win32.join("C:\\build\\venv", "Scripts", "python.exe"),
  );
});

test("package scripts no longer depend on PowerShell", async () => {
  const packageDocument = JSON.parse(
    await readFile(path.join(repoRoot, "package.json"), "utf8"),
  );
  for (const name of [
    "acceptance:linux",
    "backend:sidecar",
    "backend:sidecar:test",
    "tauri:dev",
    "tauri:build",
    "tauri:build:linux:local",
  ]) {
    assert.match(packageDocument.scripts[name], /^node scripts\//);
    assert.doesNotMatch(packageDocument.scripts[name], /powershell|pwsh/i);
  }
  const localConfig = JSON.parse(
    await readFile(
      path.join(repoRoot, "scripts", "tauri.local.conf.json"),
      "utf8",
    ),
  );
  assert.equal(localConfig.bundle?.createUpdaterArtifacts, false);
  assert.equal(
    packageDocument.scripts["tauri:build:linux:local"],
    "node scripts/build-linux-local.mjs",
  );
  assert.equal(
    packageDocument.scripts["acceptance:linux"],
    "node scripts/test-linux-acceptance.mjs",
  );
});

test("Linux acceptance arguments keep the exact staged AppDir and reports local", () => {
  const portableRoot = path.resolve("/portable/repo");
  const portableData = path.resolve("/portable/data");
  const options = parseAcceptanceArguments([], {
    root: portableRoot,
    environment: { XDG_DATA_HOME: portableData },
  });
  assert.equal(
    options.appDir,
    path.join(
      portableRoot,
      "src-tauri",
      "target",
      "release",
      "bundle",
      "appimage",
      "master.AppDir",
    ),
  );
  assert.equal(
    options.chatterboxRoot,
    path.join(
      portableData,
      "com.master.desktop",
      "voice-engines",
      "chatterbox",
    ),
  );
  assert.equal(
    options.reportBase,
    path.join(
      portableRoot,
      "release",
      "local",
      `Project-Master-0.3.0-linux-${linuxArtifactArchitecture()}-acceptance`,
    ),
  );
  assert.equal(options.modelTimeoutSeconds, 600);
  assert.throws(
    () =>
      parseAcceptanceArguments(["--ollama-url", "https://example.com"], {
        root: portableRoot,
      }),
    /loopback/,
  );
});

test("Linux acceptance unloads only harness-owned Ollama models", async () => {
  const resident = new Set(["owned:latest", "foreign:latest"]);
  const unloadRequests = [];
  const fakeFetch = async (url, options = {}) => {
    const route = new URL(url).pathname;
    if ((options.method ?? "GET") === "GET" && route === "/api/ps") {
      return new Response(
        JSON.stringify({ models: [...resident].map((name) => ({ name })) }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    if (options.method === "POST" && route === "/api/generate") {
      const payload = JSON.parse(options.body);
      unloadRequests.push(payload);
      if (payload.keep_alive === 0) resident.delete(payload.model);
      return new Response(JSON.stringify({ done: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ error: "not found" }), { status: 404 });
  };
  const result = await unloadHarnessOllamaModels(
    "http://127.0.0.1:11434",
    ["owned:latest"],
    fakeFetch,
  );
  assert.deepEqual(result, {
    unloaded: ["owned:latest"],
    foreign: ["foreign:latest"],
  });
  assert.deepEqual(unloadRequests, [
    {
      model: "owned:latest",
      keep_alive: 0,
      stream: false,
    },
  ]);
  assert.deepEqual([...resident], ["foreign:latest"]);
});

test("Linux acceptance launch environment is isolated and rejects inherited MASTER state", () => {
  const appDir = "/candidate/master.AppDir";
  const profile = {
    home: "/fixture/home",
    data: "/fixture/data",
    config: "/fixture/config",
    cache: "/fixture/cache",
    temp: "/fixture/tmp",
    configPath: "/fixture/config.yaml",
    dbPath: "/fixture/master.db",
    workspace: "/fixture/workspace",
    logPath: "/fixture/backend.log",
  };
  const environment = buildBackendEnvironment({
    appDir,
    profile,
    port: 43123,
    sessionToken: "isolated-token",
    ollamaUrl: "http://127.0.0.1:11434",
    baseEnvironment: {
      PATH: "/usr/bin",
      LD_LIBRARY_PATH: "/host/lib",
      MASTER_DB_PATH: "/real/master.db",
      MASTER_SESSION_TOKEN: "real-token",
      HTTP_PROXY: "http://proxy.invalid",
      HF_TOKEN: "real-hugging-face-token",
      OPENAI_API_KEY: "real-api-key",
      KEEP_ME: "yes",
    },
  });

  assert.equal(environment.KEEP_ME, "yes");
  assert.equal(environment.HOME, profile.home);
  assert.equal(environment.MASTER_DB_PATH, profile.dbPath);
  assert.equal(environment.MASTER_SESSION_TOKEN, "isolated-token");
  assert.equal(environment.APPDIR, appDir);
  assert.equal(environment.PYTHONHOME, path.join(appDir, "usr"));
  assert.equal(environment.LD_LIBRARY_PATH_ORIG, "/host/lib");
  assert.equal(
    environment.PATH.split(path.delimiter)[0],
    path.join(appDir, "usr", "bin"),
  );
  assert.equal(environment.HF_HUB_OFFLINE, "1");
  assert.equal(environment.TRANSFORMERS_OFFLINE, "1");
  assert.equal(environment.NO_PROXY, "127.0.0.1,localhost,::1");
  assert.equal(environment.no_proxy, "127.0.0.1,localhost,::1");
  assert.equal(environment.HTTP_PROXY, undefined);
  assert.equal(environment.HF_TOKEN, undefined);
  assert.equal(environment.OPENAI_API_KEY, undefined);
});

test(
  "Linux acceptance creates a COW Chatterbox clone and verifies inventory hashes",
  { skip: process.platform !== "linux" },
  async () => {
    const fixtureParent = path.join(repoRoot, "release", "local");
    await mkdir(fixtureParent, { recursive: true });
    const fixture = await mkdtemp(
      path.join(fixtureParent, ".project-master-chatterbox-clone-"),
    );
    try {
      const source = path.join(fixture, "source");
      const destination = path.join(fixture, "destination");
      const modelPath = path.join(source, "models", "weights.bin");
      const pkusegPath = path.join(source, "pkuseg", "dictionary.txt");
      await Promise.all([
        mkdir(path.dirname(modelPath), { recursive: true }),
        mkdir(path.dirname(pkusegPath), { recursive: true }),
        mkdir(path.join(source, "venv"), { recursive: true }),
      ]);
      const modelBytes = Buffer.from("model-asset");
      const pkusegBytes = Buffer.from("pkuseg-asset");
      await Promise.all([
        writeFile(modelPath, modelBytes),
        writeFile(pkusegPath, pkusegBytes),
      ]);
      const assets = [
        ["models/weights.bin", modelBytes],
        ["pkuseg/dictionary.txt", pkusegBytes],
      ].map(([relativePath, content], index) => ({
        logical_name: `asset-${index}`,
        relative_path: relativePath,
        sha256: createHash("sha256").update(content).digest("hex"),
        size_bytes: content.length,
      }));
      await writeFile(
        path.join(source, "asset-inventory.json"),
        `${JSON.stringify({
          schema_version: 1,
          engine_source_revision: "test-revision",
          assets,
        })}\n`,
      );

      const result = await prepareChatterboxClone(source, destination);

      assert.equal(result.assetCount, 2);
      assert.equal(result.sourceRevision, "test-revision");
      assert.deepEqual(result.assetDigests, assets.map((item) => item.sha256));
      assert.equal(
        (await lstat(path.join(destination, "venv"))).isSymbolicLink(),
        true,
      );
      await writeFile(path.join(destination, "models", "weights.bin"), "changed");
      assert.deepEqual(await readFile(modelPath), modelBytes);

      const corruptDestination = path.join(fixture, "corrupt-destination");
      assets[0].sha256 = "0".repeat(64);
      await writeFile(
        path.join(source, "asset-inventory.json"),
        `${JSON.stringify({
          schema_version: 1,
          engine_source_revision: "test-revision",
          assets,
        })}\n`,
      );
      await assert.rejects(
        prepareChatterboxClone(source, corruptDestination),
        /digest does not match/,
      );
    } finally {
      await rm(fixture, { recursive: true, force: true });
    }
  },
);

test("Linux acceptance removes a partial isolated profile after setup failure", async () => {
  const parent = path.join(repoRoot, "release", "local");
  await mkdir(parent, { recursive: true });
  const prefix = ".project-master-linux-acceptance-";
  const before = new Set(
    (await readdir(parent)).filter((name) => name.startsWith(prefix)),
  );
  const invalidTimeout = {
    toString() {
      throw new Error("forced profile setup failure");
    },
  };

  await assert.rejects(
    prepareIsolatedProfile({}, invalidTimeout),
    /forced profile setup failure/,
  );

  const after = new Set(
    (await readdir(parent)).filter((name) => name.startsWith(prefix)),
  );
  assert.deepEqual(after, before);
  assert.equal(
    await safeAcceptanceRoot(
      path.join(parent, `${prefix}fixture`),
      parent,
    ),
    true,
  );
  assert.equal(await safeAcceptanceRoot(repoRoot, parent), false);
});

test(
  "sidecar cleanup terminates descendants after the detached leader exits",
  { skip: process.platform === "win32" },
  async () => {
    const leader = spawn(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        [
          "import { spawn } from 'node:child_process';",
          "const child = spawn(process.execPath,",
          "  ['-e', 'setInterval(() => {}, 1000)'],",
          "  { stdio: 'ignore' });",
          "child.unref();",
        ].join("\n"),
      ],
      {
        detached: true,
        stdio: "ignore",
      },
    );
    const leaderClosed = once(leader, "close", {
      signal: AbortSignal.timeout(5_000),
    });
    try {
      await waitForExit(leader, 5_000);
      await leaderClosed;
      assert.equal(leader.exitCode, 0);
      assert.equal(processGroupIsRunning(leader.pid), true);

      const stopped = await stopProcessTree(leader, 2_000);

      assert.equal(stopped.signal, "SIGTERM");
      assert.equal(stopped.forced, false);
      assert.equal(processGroupIsRunning(leader.pid), false);
    } finally {
      if (processGroupIsRunning(leader.pid)) {
        process.kill(-leader.pid, "SIGKILL");
      }
    }
  },
);

test("Linux acceptance catalog partitions aliases and plans one physical outcome", () => {
  const status = {
    ollama_reachable: true,
    configured_model: "missing:latest",
    models: ["chat:a", "chat:latest", "embed:latest"],
    catalog: [
      {
        physical_id: "digest:abc",
        tags: ["chat:a", "chat:latest"],
        primary_tag: "chat:a",
        digest: "ABC",
        size_bytes: 100,
        capabilities: ["completion", "tools"],
        details: { family: "qwen", families: ["qwen"] },
        inspection_error: null,
      },
      {
        physical_id: "digest:def",
        tags: ["embed:latest"],
        primary_tag: "embed:latest",
        digest: "DEF",
        size_bytes: 50,
        capabilities: ["embedding"],
        details: { family: "bert", families: ["bert"] },
        inspection_error: null,
      },
    ],
  };
  const direct = {
    models: [
      { name: "chat:a", digest: "ABC" },
      { name: "chat:latest", digest: "abc" },
      { name: "embed:latest", digest: "DEF" },
    ],
  };

  const validated = validateCatalogPartition(status, direct);

  assert.equal(validated.rawTagCount, 3);
  assert.equal(validated.physicalModelCount, 2);
  assert.equal(catalogModelSupportsCompletion(status.catalog[0]), true);
  assert.equal(catalogModelSupportsTools(status.catalog[0]), true);
  assert.equal(catalogModelSupportsCompletion(status.catalog[1]), false);
  assert.equal(selectToolModel(status.catalog)?.primary_tag, "chat:a");
  assert.throws(
    () =>
      validateCatalogPartition(
        {
          ...status,
          catalog: [
            ...status.catalog,
            {
              ...status.catalog[0],
              physical_id: "digest:duplicate",
            },
          ],
        },
        direct,
      ),
    /assigned more than once/,
  );
});

test("Linux acceptance reports redact launch tokens in JSON and Markdown", () => {
  const token = "session-token-must-not-leak";
  const report = {
    expected_version: "0.3.0",
    status: "failed",
    started_at: "2026-07-27T00:00:00.000Z",
    finished_at: "2026-07-27T00:00:01.000Z",
    api_version: "0.3.0",
    gui_launched: false,
    candidate: {
      backend: {
        relative_path: "master.AppDir/usr/bin/project-master-backend",
        size_bytes: 1,
        sha256: "a".repeat(64),
      },
    },
    checks: [
      {
        title: "Injected failure",
        status: "failed",
        duration_ms: 1,
        detail: `header contained ${token}`,
      },
    ],
    models: [],
    warnings: [`redact ${token}`],
  };

  const sanitized = sanitizeReportValue(report, [token]);
  const json = JSON.stringify(sanitized);
  const markdown = renderAcceptanceMarkdown(sanitized);

  assert.doesNotMatch(json, new RegExp(token));
  assert.doesNotMatch(markdown, new RegExp(token));
  assert.match(json, /\[REDACTED\]/);
  assert.match(markdown, /\[REDACTED\]/);
});

test("Linux acceptance writes private paired reports without temp debris", async () => {
  const fixture = await mkdtemp(
    path.join(os.tmpdir(), "project-master-acceptance-report-"),
  );
  const secret = "report-session-token";
  const reportBase = path.join(fixture, "candidate-acceptance");
  const report = {
    expected_version: "0.3.0",
    status: "passed",
    started_at: "2026-07-27T00:00:00.000Z",
    finished_at: "2026-07-27T00:00:01.000Z",
    api_version: "0.3.0",
    gui_launched: false,
    candidate: {
      backend: {
        relative_path: "master.AppDir/usr/bin/project-master-backend",
        size_bytes: 1,
        sha256: "a".repeat(64),
      },
    },
    checks: [{
      title: "Redacted evidence",
      status: "passed",
      duration_ms: 1,
      detail: `token=${secret}`,
    }],
    models: [],
    warnings: [`secret ${secret}`],
  };
  try {
    const paths = await writeReports(report, reportBase, [secret]);
    const [jsonText, markdownText, jsonStat, markdownStat] = await Promise.all([
      readFile(paths.jsonPath, "utf8"),
      readFile(paths.markdownPath, "utf8"),
      stat(paths.jsonPath),
      stat(paths.markdownPath),
    ]);

    assert.doesNotThrow(() => JSON.parse(jsonText));
    assert.doesNotMatch(jsonText, new RegExp(secret));
    assert.doesNotMatch(markdownText, new RegExp(secret));
    if (process.platform !== "win32") {
      assert.equal(jsonStat.mode & 0o777, 0o600);
      assert.equal(markdownStat.mode & 0o777, 0o600);
    }
    assert.deepEqual(
      (await readdir(fixture)).sort(),
      ["candidate-acceptance.json", "candidate-acceptance.md"],
    );

    report.status = "failed";
    await writeReports(report, reportBase, [secret]);
    assert.equal(JSON.parse(await readFile(paths.jsonPath, "utf8")).status, "failed");
    assert.deepEqual(
      (await readdir(fixture)).sort(),
      ["candidate-acceptance.json", "candidate-acceptance.md"],
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("Linux acceptance verifies voice WAV bytes, hash, and provenance", () => {
  const content = Buffer.concat([
    Buffer.from("RIFF", "ascii"),
    Buffer.alloc(60, 7),
  ]);
  const metadata = {
    id: "voice-artifact-test",
    verified: true,
    media_type: "audio/wav",
    format: "wav",
    size_bytes: content.length,
    sha256: createHash("sha256").update(content).digest("hex"),
    duration_seconds: 0.25,
    provenance: {
      engine_id: "chatterbox",
      rights_basis: "synthetic_reference",
      model_asset_digests: Array.from(
        { length: 6 },
        (_item, index) => index.toString(16).padStart(64, "0"),
      ),
    },
  };

  const result = validateVoiceArtifact(metadata, content, {
    engineId: "chatterbox",
    expectedModelAssetCount: 6,
    expectedModelAssetDigests: metadata.provenance.model_asset_digests,
    expectedRightsBasis: "synthetic_reference",
  });

  assert.equal(result.sizeBytes, content.length);
  assert.equal(result.modelAssetCount, 6);
  assert.throws(
    () =>
      validateVoiceArtifact(
        { ...metadata, size_bytes: content.length + 1 },
        content,
        { engineId: "chatterbox", expectedModelAssetCount: 6 },
      ),
    /byte count/,
  );
  assert.throws(
    () =>
      validateVoiceArtifact(metadata, content, {
        engineId: "chatterbox",
        expectedModelAssetCount: 6,
        expectedModelAssetDigests: [
          "f".repeat(64),
          ...metadata.provenance.model_asset_digests.slice(1),
        ],
      }),
    /verified inventory/,
  );
});

test("Linux acceptance uses a truthful synthetic Chatterbox reference basis", async () => {
  const source = await readFile(
    path.join(repoRoot, "scripts", "test-linux-acceptance.mjs"),
    "utf8",
  );
  assert.match(source, /rights_basis: "synthetic_reference"/);
  assert.doesNotMatch(
    source,
    /rights_basis: "licensed_voice"[\s\S]{0,500}synthetic acceptance fixture/,
  );
});

test("release versions and Tauri Linux metadata are aligned", async () => {
  await verifyPackaging([]);
});

test("Fedora AppImage staging keeps the executable, icon, and desktop identity aligned", async () => {
  const appImageConfig = JSON.parse(
    await readFile(
      path.join(repoRoot, "scripts", "tauri.appimage.conf.json"),
      "utf8",
    ),
  );
  const desktop = await readFile(
    path.join(
      repoRoot,
      "src-tauri",
      "linux",
      "project-master-appimage.desktop",
    ),
    "utf8",
  );

  assert.equal(appImageConfig.productName, "master");
  assert.equal(appImageConfig.bundle?.createUpdaterArtifacts, false);
  assert.equal(
    appImageConfig.bundle?.linux?.appimage?.files?.[
      "/usr/share/applications/master.desktop"
    ],
    "linux/project-master-appimage.desktop",
  );
  assert.match(desktop, /^Name=Project Master$/m);
  assert.match(desktop, /^Exec=master$/m);
  assert.match(desktop, /^Icon=master$/m);
});

test("local Linux orchestration builds once, skips old linuxdeploy strip, and verifies", async () => {
  const commands = localLinuxBuildCommands("/portable/repo");
  assert.deepEqual(commands.clean, {
    command: "cargo",
    args: [
      "clean",
      "--release",
      "--package",
      "master",
      "--manifest-path",
      "/portable/repo/src-tauri/Cargo.toml",
    ],
  });
  assert.deepEqual(commands.build.args, [
    "/portable/repo/scripts/run-tauri.mjs",
    "build",
    "--no-bundle",
    "--config",
    "scripts/tauri.local.conf.json",
  ]);
  assert.deepEqual(commands.rpm.args, [
    "/portable/repo/node_modules/@tauri-apps/cli/tauri.js",
    "bundle",
    "--bundles",
    "rpm",
    "--config",
    "scripts/tauri.local.conf.json",
  ]);
  assert.deepEqual(commands.appImage.args, [
    "/portable/repo/node_modules/@tauri-apps/cli/tauri.js",
    "bundle",
    "--bundles",
    "appimage",
    "--config",
    "scripts/tauri.appimage.conf.json",
  ]);
  assert.deepEqual(commands.verify.args, [
    "/portable/repo/scripts/verify-packaging.mjs",
    "--sidecar",
    "--artifacts",
  ]);
  const script = await readFile(
    path.join(repoRoot, "scripts", "build-linux-local.mjs"),
    "utf8",
  );
  assert.match(script, /NO_STRIP:\s*"1"/);
  assert.doesNotMatch(script, /\/home\/mike|\.cache\/tauri/);
  assert.equal(
    tauriCacheDirectory(
      { XDG_CACHE_HOME: "/portable/cache" },
      "/portable/home",
    ),
    "/portable/cache/tauri",
  );
  assert.equal(
    appImageOutputPluginPath(
      { PROJECT_MASTER_TAURI_CACHE_DIR: "/custom/tauri-cache" },
      "/portable/home",
    ),
    "/custom/tauri-cache/linuxdeploy-plugin-appimage.AppImage",
  );
});

test("AppImage finalization validates the staged identity and creates one branded artifact", async () => {
  const fixture = await mkdtemp(path.join(os.tmpdir(), "project-master-appimage-"));
  try {
    const appDir = path.join(fixture, "master.AppDir");
    await Promise.all([
      mkdir(path.join(appDir, "usr", "bin"), { recursive: true }),
      mkdir(path.join(appDir, "usr", "lib"), { recursive: true }),
      mkdir(path.join(appDir, "usr", "libexec", "webkit2gtk-4.1"), {
        recursive: true,
      }),
    ]);
    await Promise.all([
      writeFile(
        path.join(appDir, "master.desktop"),
        [
          "[Desktop Entry]",
          "Name=Project Master",
          "Exec=master",
          "Icon=master",
          "Type=Application",
          "Terminal=false",
          "",
        ].join("\n"),
      ),
      writeFile(path.join(appDir, "master.png"), "icon"),
    ]);
    await Promise.all([
      writeFile(
        path.join(appDir, "usr", "bin", "master"),
        `desktop ${BUNDLE_MARKERS.appImage}`,
      ),
      writeFile(
        path.join(appDir, "usr", "bin", "project-master-backend"),
        "sidecar",
      ),
      writeFile(path.join(appDir, "AppRun"), "launcher"),
      writeFile(
        path.join(appDir, "usr", "lib", "libwebkit2gtk-4.1.so.0"),
        "webkit",
      ),
      writeFile(
        path.join(
          appDir,
          "usr",
          "libexec",
          "webkit2gtk-4.1",
          "WebKitWebProcess",
        ),
        "webkit-process",
      ),
      writeFile(
        path.join(appDir, "usr", "lib", "libgstreamer-1.0.so.0"),
        "gstreamer",
      ),
      writeFile(path.join(fixture, "master_0.3.0_amd64.AppImage"), "image"),
    ]);

    const finalized = await finalizeLinuxAppImage({
      bundleRoot: fixture,
      version: "0.3.0",
      architecture: "x64",
    });
    assert.equal(
      finalized,
      path.join(fixture, brandedAppImageName("0.3.0", "x64")),
    );
    assert.equal((await stat(finalized)).size, 5);
    await assert.rejects(
      access(path.join(fixture, "master_0.3.0_amd64.AppImage")),
    );
    await verifyBundleMarker(
      path.join(appDir, "usr", "bin", "master"),
      BUNDLE_MARKERS.appImage,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});
