import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  constants as fsConstants,
  copyFile,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  linuxArtifactArchitecture,
  verifyStagedAppDir,
} from "./build-linux-local.mjs";
import {
  ensureExecutable,
  isMain,
  repoRoot,
} from "./lib/platform.mjs";
import {
  appendBounded,
  cleanChildEnvironment,
  freePort,
  stopProcessTree,
  waitForPortClosed,
  waitForReady,
} from "./test-backend-sidecar.mjs";

const EXPECTED_VERSION = "0.3.0";
const BINDER_CODEWORD = "ORBITAL-PINE-731";
const MODEL_RESPONSE_CODEWORD = "PROJECT_MASTER_MODEL_OK";
const AUTHORIZED_MUTATION_CONTENT = "AUTHORIZED_WORKSPACE_MUTATION_V030";
const REPORT_SCHEMA_VERSION = 1;
const COMPLETION_CAPABILITIES = new Set(["chat", "completion", "generate"]);
const TOOL_CAPABILITIES = new Set(["tool", "tools", "tool_calling"]);
const TERMINAL_DREAM_STATUSES = new Set([
  "complete",
  "partial",
  "failed",
  "cancelled",
]);
const EXPECTED_TOOL_NAMES = new Set([
  "calculator",
  "claim_record",
  "claims_list",
  "comfy_connection_status",
  "comfy_connections_list",
  "comfy_queue_status",
  "comfy_run_artifacts",
  "comfy_run_cancel",
  "comfy_run_status",
  "comfy_workflow_run",
  "comfy_workflow_validate",
  "comfy_workflows_list",
  "current_time",
  "dream_inbox_list",
  "dream_recipes_list",
  "dream_run_manual",
  "dream_runs_list",
  "evidence_add",
  "knowledge_search",
  "memory_recall",
  "memory_remember",
  "terminal_run",
  "voice_artifacts_list",
  "voice_engine_health",
  "voice_render_cancel",
  "voice_render_create",
  "voice_render_run",
  "voice_render_status",
  "voice_studio_status",
  "workspace_list",
  "workspace_read",
  "workspace_write",
]);

function defaultChatterboxRoot(environment = process.env) {
  const dataHome = environment.XDG_DATA_HOME
    ? path.resolve(environment.XDG_DATA_HOME)
    : path.join(os.homedir(), ".local", "share");
  return path.join(
    dataHome,
    "com.master.desktop",
    "voice-engines",
    "chatterbox",
  );
}

function parseIntegerOption(name, raw, minimum, maximum) {
  const value = Number(raw);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(
      `${name} must be an integer from ${minimum} through ${maximum}.`,
    );
  }
  return value;
}

function requireLoopbackUrl(raw) {
  const value = new URL(raw);
  const hostname = value.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (
    value.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1"].includes(hostname) ||
    value.username ||
    value.password ||
    (value.pathname !== "/" && value.pathname !== "")
  ) {
    throw new Error("--ollama-url must be an unauthenticated loopback HTTP origin.");
  }
  return value.origin;
}

export function parseAcceptanceArguments(
  argv,
  {
    root = repoRoot,
    environment = process.env,
  } = {},
) {
  const architecture = linuxArtifactArchitecture();
  const options = {
    appDir: path.join(
      root,
      "src-tauri",
      "target",
      "release",
      "bundle",
      "appimage",
      "master.AppDir",
    ),
    reportBase: path.join(
      root,
      "release",
      "local",
      `Project-Master-${EXPECTED_VERSION}-linux-${architecture}-acceptance`,
    ),
    chatterboxRoot: defaultChatterboxRoot(environment),
    ollamaUrl: "http://127.0.0.1:11434",
    startupTimeoutSeconds: 90,
    requestTimeoutSeconds: 30,
    modelTimeoutSeconds: 600,
    dreamTimeoutSeconds: 1_800,
    voiceTimeoutSeconds: 900,
    overallTimeoutSeconds: 7_200,
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
    const raw = nextValue.bind(null, argument, index);
    if (argument === "--app-dir") {
      options.appDir = path.resolve(raw());
      index += 1;
    } else if (argument === "--report-base") {
      options.reportBase = path.resolve(raw().replace(/\.(?:json|md)$/i, ""));
      index += 1;
    } else if (argument === "--chatterbox-root") {
      options.chatterboxRoot = path.resolve(raw());
      index += 1;
    } else if (argument === "--ollama-url") {
      options.ollamaUrl = requireLoopbackUrl(raw());
      index += 1;
    } else if (argument === "--startup-timeout") {
      options.startupTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        10,
        300,
      );
      index += 1;
    } else if (argument === "--request-timeout") {
      options.requestTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        5,
        120,
      );
      index += 1;
    } else if (argument === "--model-timeout") {
      options.modelTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        30,
        900,
      );
      index += 1;
    } else if (argument === "--dream-timeout") {
      options.dreamTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        60,
        3_600,
      );
      index += 1;
    } else if (argument === "--voice-timeout") {
      options.voiceTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        60,
        1_800,
      );
      index += 1;
    } else if (argument === "--overall-timeout") {
      options.overallTimeoutSeconds = parseIntegerOption(
        argument,
        raw(),
        600,
        14_400,
      );
      index += 1;
    } else {
      throw new Error(`Unknown Linux acceptance argument: ${argument}`);
    }
  }
  return options;
}

function normalizedCapabilities(model) {
  return new Set(
    Array.isArray(model?.capabilities)
      ? model.capabilities
        .filter((item) => typeof item === "string")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean)
      : [],
  );
}

export function catalogModelSupportsCompletion(model) {
  const capabilities = normalizedCapabilities(model);
  if ([...capabilities].some((item) => COMPLETION_CAPABILITIES.has(item))) {
    return true;
  }
  if (capabilities.size) {
    return false;
  }
  const familyTokens = [
    model?.details?.family,
    ...(Array.isArray(model?.details?.families)
      ? model.details.families
      : []),
  ]
    .filter((item) => typeof item === "string")
    .map((item) => item.toLowerCase());
  return !familyTokens.some((item) => item.includes("embed"));
}

export function catalogModelSupportsTools(model) {
  return [...normalizedCapabilities(model)]
    .some((item) => TOOL_CAPABILITIES.has(item));
}

function rawModelTag(item) {
  if (!item || typeof item !== "object") return "";
  const value = item.name ?? item.model;
  return typeof value === "string" ? value.trim() : "";
}

function physicalKey(item) {
  const digest =
    typeof item?.digest === "string" ? item.digest.trim().toLowerCase() : "";
  const tag = rawModelTag(item);
  return digest ? `digest:${digest}` : `tag:${tag.toLowerCase()}`;
}

function sortedUnique(values) {
  return [...new Set(values)].sort((left, right) =>
    left.localeCompare(right, undefined, { sensitivity: "base" })
  );
}

export function validateCatalogPartition(statusPayload, ollamaTagsPayload) {
  const rawTags = Array.isArray(statusPayload?.models)
    ? statusPayload.models.filter((item) => typeof item === "string" && item)
    : [];
  const catalog = Array.isArray(statusPayload?.catalog)
    ? statusPayload.catalog
    : [];
  const directModels = Array.isArray(ollamaTagsPayload?.models)
    ? ollamaTagsPayload.models
    : [];
  if (statusPayload?.ollama_reachable !== true) {
    throw new Error("The packaged backend reports Ollama as unreachable.");
  }
  if (!rawTags.length || !catalog.length || !directModels.length) {
    throw new Error("The Ollama model catalog is empty.");
  }
  if (rawTags.length !== new Set(rawTags).size) {
    throw new Error("The backend raw Ollama tag list contains duplicates.");
  }
  const directTags = directModels.map(rawModelTag).filter(Boolean);
  if (
    JSON.stringify(sortedUnique(rawTags)) !==
    JSON.stringify(sortedUnique(directTags))
  ) {
    throw new Error("The backend raw tag list does not match Ollama /api/tags.");
  }

  const seenPhysicalIds = new Set();
  const aliasOwners = new Map();
  for (const model of catalog) {
    if (
      !model ||
      typeof model.physical_id !== "string" ||
      !Array.isArray(model.tags) ||
      !model.tags.length ||
      typeof model.primary_tag !== "string"
    ) {
      throw new Error("The backend catalog contains an invalid physical model.");
    }
    if (seenPhysicalIds.has(model.physical_id)) {
      throw new Error(`Duplicate physical model ID: ${model.physical_id}`);
    }
    seenPhysicalIds.add(model.physical_id);
    if (!model.tags.includes(model.primary_tag)) {
      throw new Error(
        `Catalog primary tag is absent from aliases: ${model.primary_tag}`,
      );
    }
    for (const alias of model.tags) {
      if (aliasOwners.has(alias)) {
        throw new Error(`Ollama alias is assigned more than once: ${alias}`);
      }
      aliasOwners.set(alias, model.physical_id);
    }
  }
  if (
    JSON.stringify(sortedUnique(aliasOwners.keys())) !==
    JSON.stringify(sortedUnique(rawTags))
  ) {
    throw new Error("Catalog aliases do not partition every raw Ollama tag.");
  }

  const expectedGroups = new Map();
  for (const item of directModels) {
    const tag = rawModelTag(item);
    if (!tag) continue;
    const key = physicalKey(item);
    expectedGroups.set(
      key,
      sortedUnique([...(expectedGroups.get(key) ?? []), tag]),
    );
  }
  const actualGroups = catalog
    .map((item) => sortedUnique(item.tags))
    .sort((left, right) => left.join("\0").localeCompare(right.join("\0")));
  const directGroups = [...expectedGroups.values()]
    .sort((left, right) => left.join("\0").localeCompare(right.join("\0")));
  if (JSON.stringify(actualGroups) !== JSON.stringify(directGroups)) {
    throw new Error(
      "The packaged digest/alias grouping does not match Ollama /api/tags.",
    );
  }
  return {
    rawTagCount: rawTags.length,
    physicalModelCount: catalog.length,
    aliasGroupCount: directGroups.length,
    catalog,
    inspectionErrors: catalog
      .filter((item) => item.inspection_error)
      .map((item) => ({
        primary_tag: item.primary_tag,
        detail: String(item.inspection_error),
      })),
  };
}

export function selectToolModel(catalog, configuredModel = "") {
  const candidates = catalog.filter(
    (item) =>
      !item.inspection_error &&
      catalogModelSupportsCompletion(item) &&
      catalogModelSupportsTools(item),
  );
  if (!candidates.length) return null;
  const configured = candidates.find((item) =>
    item.tags.some(
      (tag) => tag.toLowerCase() === configuredModel.toLowerCase(),
    )
  );
  if (configured) return configured;
  const preferred = candidates.find((item) =>
    item.tags.some((tag) => /^dolphin-96k:/i.test(tag))
  );
  if (preferred) return preferred;
  return [...candidates].sort(
    (left, right) =>
      Number(left.size_bytes ?? Number.MAX_SAFE_INTEGER) -
        Number(right.size_bytes ?? Number.MAX_SAFE_INTEGER) ||
      left.primary_tag.localeCompare(right.primary_tag),
  )[0];
}

function boundedText(value, limit = 2_000) {
  const text = String(value ?? "");
  return text.length <= limit
    ? text
    : `${text.slice(0, Math.max(0, limit - 14))}\n[truncated]`;
}

function redactString(value, secrets) {
  let result = String(value);
  for (const secret of secrets) {
    if (secret) result = result.split(secret).join("[REDACTED]");
  }
  return result;
}

export function sanitizeReportValue(value, secrets = []) {
  if (typeof value === "string") return redactString(value, secrets);
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeReportValue(item, secrets));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        sanitizeReportValue(item, secrets),
      ]),
    );
  }
  return value;
}

function errorMessage(error, secrets) {
  const raw = error instanceof Error ? error.message : String(error);
  return boundedText(redactString(raw, secrets));
}

function markdownCell(value) {
  return String(value ?? "")
    .replace(/\|/g, "\\|")
    .replace(/\r?\n/g, " ");
}

export function renderAcceptanceMarkdown(report) {
  const lines = [
    `# Project Master ${report.expected_version} Linux acceptance`,
    "",
    `**Status:** ${String(report.status).toUpperCase()}`,
    "",
    `Started: ${report.started_at}`,
    "",
    `Finished: ${report.finished_at ?? "not finished"}`,
    "",
    "## Candidate",
    "",
    "| Field | Value |",
    "| --- | --- |",
    `| Staged backend | ${markdownCell(report.candidate.backend.relative_path)} |`,
    `| Bytes | ${markdownCell(report.candidate.backend.size_bytes)} |`,
    `| SHA-256 | \`${markdownCell(report.candidate.backend.sha256)}\` |`,
    `| API version | ${markdownCell(report.api_version ?? "unconfirmed")} |`,
    `| GUI launched | ${report.gui_launched ? "yes" : "no"} |`,
    "",
    "## Checks",
    "",
    "| Check | Status | Duration | Detail |",
    "| --- | --- | ---: | --- |",
  ];
  for (const check of report.checks) {
    lines.push(
      `| ${markdownCell(check.title)} | ${markdownCell(check.status)} | ` +
        `${markdownCell(check.duration_ms)} ms | ${markdownCell(check.detail)} |`,
    );
  }
  lines.push(
    "",
    "## Physical Ollama models",
    "",
    "| Primary tag | Aliases | Status | Duration | Detail |",
    "| --- | --- | --- | ---: | --- |",
  );
  if (!report.models.length) {
    lines.push("| — | — | not run | 0 ms | Catalog unavailable |");
  } else {
    for (const model of report.models) {
      lines.push(
        `| ${markdownCell(model.primary_tag)} | ` +
          `${markdownCell(model.aliases.join(", "))} | ` +
          `${markdownCell(model.status)} | ${markdownCell(model.duration_ms)} ms | ` +
          `${markdownCell(model.detail)} |`,
      );
    }
  }
  if (report.warnings.length) {
    lines.push("", "## Warnings", "");
    for (const warning of report.warnings) {
      lines.push(`- ${warning}`);
    }
  }
  lines.push(
    "",
    "## Policy",
    "",
    "- A non-completion model may be recorded as an intentional skip.",
    "- A conversational model timeout, API error, or empty response fails the gate.",
    "- ComfyUI being explicitly offline is a supported passing state.",
    "- The desktop GUI remained closed for this headless run.",
    "",
  );
  return lines.join("\n");
}

class GateRecorder {
  constructor(report, secrets, interruption = () => null) {
    this.report = report;
    this.secrets = secrets;
    this.interruption = interruption;
  }

  async run(id, title, action) {
    const started = Date.now();
    try {
      const beforeAction = this.interruption();
      if (beforeAction) throw beforeAction;
      const raw = await action();
      const afterAction = this.interruption();
      if (afterAction) throw afterAction;
      const normalized =
        raw &&
        typeof raw === "object" &&
        Object.hasOwn(raw, "evidence") &&
        Object.hasOwn(raw, "value")
          ? raw
          : { evidence: raw ?? {}, value: raw };
      const check = {
        id,
        title,
        status: "passed",
        duration_ms: Date.now() - started,
        detail: normalized.evidence?.detail ?? "Passed",
        evidence: normalized.evidence ?? {},
      };
      this.report.checks.push(sanitizeReportValue(check, this.secrets));
      console.log(`[PASS] ${title}`);
      return { ok: true, value: normalized.value };
    } catch (error) {
      const detail = errorMessage(error, this.secrets);
      this.report.checks.push({
        id,
        title,
        status: "failed",
        duration_ms: Date.now() - started,
        detail,
        evidence: {},
      });
      console.error(`[FAIL] ${title}: ${detail}`);
      const interrupted = this.interruption();
      if (interrupted) throw interrupted;
      return { ok: false, error };
    }
  }

  skip(id, title, detail) {
    const interrupted = this.interruption();
    if (interrupted) throw interrupted;
    this.report.checks.push({
      id,
      title,
      status: "skipped",
      duration_ms: 0,
      detail,
      evidence: {},
    });
    console.log(`[SKIP] ${title}: ${detail}`);
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function settleAll(promises, message) {
  const results = await Promise.allSettled(promises);
  const errors = results
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  if (errors.length) throw new AggregateError(errors, message);
  return results.map((result) => result.value);
}

async function sha256File(filePath) {
  const hash = createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.once("error", reject);
    stream.once("end", resolve);
  });
  return hash.digest("hex");
}

function sha256Bytes(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function pathIsDirectory(filePath) {
  const details = await stat(filePath).catch(() => null);
  return Boolean(details?.isDirectory());
}

export async function safeAcceptanceRoot(directory, parent) {
  const resolvedParent = path.resolve(parent);
  const resolved = path.resolve(directory);
  return (
    path.dirname(resolved) === resolvedParent &&
    path.basename(resolved).startsWith(".project-master-linux-acceptance-")
  );
}

export async function prepareChatterboxClone(sourceRoot, destinationRoot) {
  const source = path.resolve(sourceRoot);
  const inventoryPath = path.join(source, "asset-inventory.json");
  const inventory = JSON.parse(await readFile(inventoryPath, "utf8"));
  assert(
    inventory?.schema_version === 1 && Array.isArray(inventory.assets),
    "The installed Chatterbox asset inventory is invalid.",
  );
  for (const directory of ["models", "pkuseg", "venv"]) {
    assert(
      await pathIsDirectory(path.join(source, directory)),
      `The installed Chatterbox ${directory} directory is missing.`,
    );
  }
  await mkdir(destinationRoot, { recursive: true });
  const cloneOptions = {
    recursive: true,
    force: false,
    errorOnExist: true,
    preserveTimestamps: true,
    verbatimSymlinks: true,
    mode: fsConstants.COPYFILE_FICLONE_FORCE,
  };
  await settleAll([
    cp(
      path.join(source, "models"),
      path.join(destinationRoot, "models"),
      cloneOptions,
    ),
    cp(
      path.join(source, "pkuseg"),
      path.join(destinationRoot, "pkuseg"),
      cloneOptions,
    ),
    copyFile(
      inventoryPath,
      path.join(destinationRoot, "asset-inventory.json"),
      fsConstants.COPYFILE_FICLONE_FORCE,
    ),
  ], "Chatterbox copy-on-write fixture setup failed.");
  for (const name of ["venv", "venv314"]) {
    const sourceVenv = path.join(source, name);
    if (await pathIsDirectory(sourceVenv)) {
      await symlink(sourceVenv, path.join(destinationRoot, name), "dir");
    }
  }
  const cloneRoot = path.resolve(destinationRoot);
  const assetDigests = [];
  for (const asset of inventory.assets) {
    const relative = String(asset.relative_path ?? "");
    const expectedDigest = String(asset.sha256 ?? "").toLowerCase();
    assert(
      /^[a-f0-9]{64}$/.test(expectedDigest),
      `Installed Chatterbox inventory has an invalid digest: ${relative}`,
    );
    const clonedPath = path.join(cloneRoot, relative);
    const resolved = await realpath(clonedPath);
    assert(
      path.relative(cloneRoot, resolved) &&
        !path.relative(cloneRoot, resolved).startsWith("..") &&
        !path.isAbsolute(path.relative(cloneRoot, resolved)),
      `Cloned Chatterbox asset escapes the isolated root: ${relative}`,
    );
    const details = await stat(clonedPath);
    assert(
      details.isFile() && details.size === asset.size_bytes,
      `Cloned Chatterbox asset does not match its inventory: ${relative}`,
    );
    const actualDigest = await sha256File(clonedPath);
    assert(
      actualDigest === expectedDigest,
      `Cloned Chatterbox asset digest does not match its inventory: ${relative}`,
    );
    assetDigests.push(actualDigest);
  }
  return {
    assetCount: inventory.assets.length,
    assetDigests,
    sourceRevision: String(inventory.engine_source_revision ?? ""),
  };
}

export async function prepareIsolatedProfile(options, modelTimeoutSeconds) {
  const parent = path.join(repoRoot, "release", "local");
  await mkdir(parent, { recursive: true });
  let root;
  try {
    root = await mkdtemp(
      path.join(parent, ".project-master-linux-acceptance-"),
    );
    if (!(await safeAcceptanceRoot(root, parent))) {
      throw new Error(
        `Refusing to use unexpected acceptance directory: ${root}`,
      );
    }
    const directories = {
      root,
      home: path.join(root, "home"),
      data: path.join(root, "data"),
      config: path.join(root, "config"),
      cache: path.join(root, "cache"),
      temp: path.join(root, "tmp"),
      workspace: path.join(root, "workspace"),
      project: path.join(root, "workspace", "acceptance-project"),
      voiceEngine: path.join(root, "voice-engines", "chatterbox"),
    };
    await settleAll(
      Object.values(directories)
        .filter((item) => item !== root && item !== directories.voiceEngine)
        .map((item) => mkdir(item, { recursive: true })),
      "Isolated acceptance profile directory setup failed.",
    );
    await writeFile(
      path.join(directories.project, "ACCEPTANCE.md"),
      [
        "# Project Master packaged Binder fixture",
        "",
        `The release-candidate verification phrase is ${BINDER_CODEWORD}.`,
        "This fixture is local, isolated, and disposable.",
        "",
      ].join("\n"),
      "utf8",
    );
    await writeFile(
      path.join(directories.project, ".env"),
      "ACCEPTANCE_SECRET_MUST_NOT_BE_INDEXED=hidden\n",
      "utf8",
    );
    const configPath = path.join(root, "config.yaml");
    await writeFile(
      configPath,
      [
        "model: qwen3:8b",
        "ollama_url: http://127.0.0.1:11434",
        "temperature: 0.0",
        "num_ctx: 8192",
        "max_response_tokens: 512",
        "team_role_max_tokens: 256",
        "max_prompt_chars: 12000",
        "max_tool_rounds: 4",
        "max_history_messages: 12",
        "allow_file_writes: true",
        "terminal_enabled: true",
        "terminal_network_enabled: false",
        "self_review: false",
        `request_timeout_seconds: ${modelTimeoutSeconds}`,
        "",
      ].join("\n"),
      "utf8",
    );
    return {
      ...directories,
      parent,
      configPath,
      dbPath: path.join(root, "master.db"),
      logPath: path.join(root, "backend.log"),
    };
  } catch (error) {
    if (root && await safeAcceptanceRoot(root, parent)) {
      try {
        await rm(root, { recursive: true, force: true });
      } catch (cleanupError) {
        throw new AggregateError(
          [error, cleanupError],
          `Isolated profile setup failed and ${root} could not be removed.`,
        );
      }
    }
    throw error;
  }
}

export function cleanInheritedEnvironment(environment) {
  return cleanChildEnvironment(environment);
}

export function buildBackendEnvironment({
  appDir,
  profile,
  port,
  sessionToken,
  ollamaUrl,
  baseEnvironment = process.env,
}) {
  const originalLibraryPath = baseEnvironment.LD_LIBRARY_PATH ?? "";
  const appLibraryPath = [
    path.join(appDir, "usr", "lib"),
    path.join(appDir, "usr", "lib64"),
    path.join(appDir, "usr", "lib32"),
  ].join(path.delimiter);
  const appBinaryPath = path.join(appDir, "usr", "bin");
  return {
    ...cleanInheritedEnvironment(baseEnvironment),
    APPDIR: appDir,
    HOME: profile.home,
    XDG_DATA_HOME: profile.data,
    XDG_CONFIG_HOME: profile.config,
    XDG_CACHE_HOME: profile.cache,
    TMPDIR: profile.temp,
    PATH: [appBinaryPath, baseEnvironment.PATH ?? ""]
      .filter(Boolean)
      .join(path.delimiter),
    PYTHONHOME: path.join(appDir, "usr"),
    PYTHONPATH: path.join(appDir, "usr", "share", "pyshared"),
    LD_LIBRARY_PATH: [
      appLibraryPath,
      originalLibraryPath,
    ].filter(Boolean).join(path.delimiter),
    LD_LIBRARY_PATH_ORIG: originalLibraryPath,
    PYTHONDONTWRITEBYTECODE: "1",
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
    NO_PROXY: "127.0.0.1,localhost,::1",
    no_proxy: "127.0.0.1,localhost,::1",
    MASTER_API_PORT: String(port),
    MASTER_CONFIG: profile.configPath,
    MASTER_DB_PATH: profile.dbPath,
    MASTER_WORKSPACE_ROOT: profile.workspace,
    MASTER_LOG_PATH: profile.logPath,
    MASTER_OLLAMA_URL: ollamaUrl,
    MASTER_ALLOW_FILE_WRITES: "true",
    MASTER_TERMINAL_ENABLED: "true",
    MASTER_TERMINAL_NETWORK_ENABLED: "false",
    MASTER_SESSION_TOKEN: sessionToken,
  };
}

async function launchBackend({
  binary,
  appDir,
  profile,
  port,
  sessionToken,
  ollamaUrl,
  startupTimeoutMs,
  phase,
  onSpawn,
}) {
  const env = buildBackendEnvironment({
    appDir,
    profile,
    port,
    sessionToken,
    ollamaUrl,
  });
  let stdout = "";
  let stderr = "";
  const child = spawn(binary, [], {
    cwd: profile.root,
    detached: true,
    env,
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (chunk) => {
    stdout = appendBounded(stdout, chunk);
  });
  child.stderr.on("data", (chunk) => {
    stderr = appendBounded(stderr, chunk);
  });
  await new Promise((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
  const launched = {
    phase,
    child,
    port,
    sessionToken,
    ready: null,
    diagnostics: async () => ({
      phase,
      stdout: boundedText(stdout, 8_000),
      stderr: boundedText(stderr, 8_000),
      log: boundedText(
        await readFile(profile.logPath, "utf8").catch(() => ""),
        8_000,
      ),
    }),
  };
  onSpawn?.(launched);
  launched.ready = await waitForReady(
    port,
    child,
    sessionToken,
    Date.now() + startupTimeoutMs,
  );
  return launched;
}

async function stopBackend(launched) {
  const termination = await stopProcessTree(launched.child, 15_000);
  await waitForPortClosed(launched.port, 15_000);
  return {
    phase: launched.phase,
    signal: termination.signal,
    forced: termination.forced,
    port_closed: true,
  };
}

function requestTimeout(deadline, requestedMs) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) {
    throw new Error("The overall Linux acceptance deadline expired.");
  }
  return Math.max(1, Math.min(requestedMs, remaining));
}

function createApiClient({
  port,
  sessionToken,
  overallDeadline,
  defaultTimeoutMs,
}) {
  const baseUrl = `http://127.0.0.1:${port}`;
  async function request(
    route,
    {
      method = "GET",
      body,
      token = sessionToken,
      expected = [200],
      timeoutMs = defaultTimeoutMs,
      signal,
    } = {},
  ) {
    const headers = {};
    if (token) headers["X-Project-Master-Token"] = token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const effectiveSignal =
      signal ??
      AbortSignal.timeout(requestTimeout(overallDeadline, timeoutMs));
    const response = await fetch(`${baseUrl}${route}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: effectiveSignal,
    });
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = raw;
      }
    }
    if (!expected.includes(response.status)) {
      const detail =
        payload && typeof payload === "object"
          ? payload.detail ?? payload.error ?? JSON.stringify(payload)
          : raw;
      throw new Error(
        `${method} ${route} returned HTTP ${response.status}: ` +
          boundedText(detail || response.statusText, 700),
      );
    }
    return { response, payload };
  }

  async function bytes(
    route,
    {
      expected = [200],
      timeoutMs = defaultTimeoutMs,
    } = {},
  ) {
    const response = await fetch(`${baseUrl}${route}`, {
      headers: { "X-Project-Master-Token": sessionToken },
      redirect: "error",
      signal: AbortSignal.timeout(
        requestTimeout(overallDeadline, timeoutMs),
      ),
    });
    if (!expected.includes(response.status)) {
      throw new Error(`GET ${route} returned HTTP ${response.status}.`);
    }
    return {
      response,
      content: Buffer.from(await response.arrayBuffer()),
    };
  }

  return { baseUrl, request, bytes };
}

async function fetchLoopbackJson(url, overallDeadline, timeoutMs) {
  const response = await fetch(url, {
    redirect: "error",
    signal: AbortSignal.timeout(
      requestTimeout(overallDeadline, timeoutMs),
    ),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(`${url} returned HTTP ${response.status}.`);
  }
  return payload;
}

function loadedOllamaModelNames(payload) {
  return sortedUnique(
    (Array.isArray(payload?.models) ? payload.models : [])
      .map((item) => rawModelTag(item))
      .filter(Boolean),
  );
}

export async function unloadHarnessOllamaModels(
  ollamaUrl,
  harnessModels,
  fetchImpl = fetch,
) {
  const request = async (route, options = {}) => {
    const response = await fetchImpl(`${ollamaUrl}${route}`, {
      ...options,
      redirect: "error",
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(
        `Ollama cleanup ${route} returned HTTP ${response.status}.`,
      );
    }
    return payload;
  };
  const before = loadedOllamaModelNames(await request("/api/ps"));
  const ownedNames = new Set(
    harnessModels.map((model) => model.toLowerCase()),
  );
  const owned = before.filter((model) => ownedNames.has(model.toLowerCase()));
  const foreign = before.filter(
    (model) => !ownedNames.has(model.toLowerCase()),
  );
  for (const model of owned) {
    await request("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        keep_alive: 0,
        stream: false,
      }),
    });
  }
  const deadline = Date.now() + 30_000;
  let remaining = owned;
  while (remaining.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    remaining = loadedOllamaModelNames(await request("/api/ps"))
      .filter((model) => ownedNames.has(model.toLowerCase()));
  }
  assert(
    remaining.length === 0,
    `Ollama still has resident model(s): ${remaining.join(", ")}`,
  );
  return { unloaded: owned, foreign };
}

async function streamChat(
  client,
  body,
  {
    timeoutMs,
    overallDeadline,
  },
) {
  const controller = new AbortController();
  let timedOut = false;
  let cancellationPromise = Promise.resolve();
  let abortTimer;
  const timeout = requestTimeout(overallDeadline, timeoutMs);
  const timer = setTimeout(() => {
    timedOut = true;
    cancellationPromise = client.request("/api/v1/chat/cancel", {
      method: "POST",
      body: { request_id: body.request_id },
      timeoutMs: 5_000,
    }).catch(() => null);
    abortTimer = setTimeout(() => controller.abort(), 5_000);
  }, timeout);
  const events = [];
  try {
    const response = await fetch(`${client.baseUrl}/api/v1/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Project-Master-Token": body.sessionToken,
      },
      body: JSON.stringify(
        Object.fromEntries(
          Object.entries(body).filter(([key]) => key !== "sessionToken"),
        ),
      ),
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(
        `POST /api/v1/chat/stream returned HTTP ${response.status}: ` +
          boundedText(detail, 700),
      );
    }
    assert(response.body, "Chat stream did not include a response body.");
    const decoder = new TextDecoder();
    let pending = "";
    for await (const chunk of response.body) {
      pending += decoder.decode(chunk, { stream: true });
      let separator;
      while ((separator = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, separator).trim();
        pending = pending.slice(separator + 1);
        if (line) events.push(JSON.parse(line));
      }
    }
    pending += decoder.decode();
    if (pending.trim()) events.push(JSON.parse(pending.trim()));
    await cancellationPromise;
    if (timedOut) {
      throw new Error(
        `Chat request ${body.request_id} exceeded its ${Math.round(timeout / 1_000)}s timeout.`,
      );
    }
  } finally {
    clearTimeout(timer);
    if (abortTimer) clearTimeout(abortTimer);
    if (!controller.signal.aborted) controller.abort();
  }
  const terminal = [...events]
    .reverse()
    .find((event) => ["done", "cancelled", "error"].includes(event.type));
  assert(terminal, "Chat stream ended without a terminal event.");
  if (terminal.type !== "done") {
    throw new Error(
      `Chat stream ended with ${terminal.type}: ${terminal.error ?? "no detail"}`,
    );
  }
  return {
    events,
    terminal,
    tools: events
      .filter((event) => event.type === "tool")
      .map((event) => event.tool),
    content: String(terminal.content ?? ""),
    start: events.find((event) => event.type === "start"),
  };
}

async function runCancellableVoiceJob(
  client,
  jobId,
  timeoutMs,
  overallDeadline,
) {
  const controller = new AbortController();
  let timedOut = false;
  let cancellationPromise = Promise.resolve();
  let abortTimer;
  const timeout = requestTimeout(overallDeadline, timeoutMs);
  const timer = setTimeout(() => {
    timedOut = true;
    cancellationPromise = client.request(
      `/api/v1/voice/jobs/${encodeURIComponent(jobId)}/cancel`,
      {
        method: "POST",
        timeoutMs: 10_000,
      },
    ).catch(() => null);
    abortTimer = setTimeout(() => controller.abort(), 10_000);
  }, timeout);
  try {
    const { payload } = await client.request(
      `/api/v1/voice/jobs/${encodeURIComponent(jobId)}/run`,
      {
        method: "POST",
        timeoutMs: timeout + 10_000,
        signal: controller.signal,
      },
    );
    await cancellationPromise;
    if (timedOut) {
      throw new Error(
        `Voice job ${jobId} exceeded its ${Math.round(timeout / 1_000)}s timeout.`,
      );
    }
    return payload;
  } finally {
    clearTimeout(timer);
    if (abortTimer) clearTimeout(abortTimer);
    if (!controller.signal.aborted) controller.abort();
  }
}

export function validateVoiceArtifact(
  metadata,
  content,
  {
    engineId,
    expectedModelAssetCount,
    expectedModelAssetDigests,
    expectedRightsBasis,
  },
) {
  assert(metadata?.verified === true, "Voice artifact is not marked verified.");
  assert(metadata?.media_type === "audio/wav", "Voice artifact is not WAV.");
  assert(metadata?.format === "wav", "Voice artifact format is not wav.");
  assert(content.length > 44, "Voice artifact content is empty.");
  assert(
    content.subarray(0, 4).toString("ascii") === "RIFF",
    "Voice artifact is missing its RIFF header.",
  );
  assert(
    metadata.size_bytes === content.length,
    "Voice artifact byte count does not match metadata.",
  );
  assert(
    metadata.sha256 === sha256Bytes(content),
    "Voice artifact SHA-256 does not match its content.",
  );
  assert(
    metadata?.provenance?.engine_id === engineId,
    `Voice artifact provenance is not ${engineId}.`,
  );
  const assetDigests = metadata?.provenance?.model_asset_digests;
  assert(
    Array.isArray(assetDigests) &&
      assetDigests.length === expectedModelAssetCount,
    `Voice artifact expected ${expectedModelAssetCount} model asset digests.`,
  );
  assert(
    assetDigests.every((digest) => /^[0-9a-f]{64}$/.test(digest)),
    "Voice artifact contains an invalid model asset digest.",
  );
  if (expectedModelAssetDigests) {
    assert(
      JSON.stringify(sortedUnique(assetDigests)) ===
        JSON.stringify(sortedUnique(expectedModelAssetDigests)),
      "Voice artifact model provenance does not match the verified inventory.",
    );
  }
  if (expectedRightsBasis) {
    assert(
      metadata?.provenance?.rights_basis === expectedRightsBasis,
      `Voice artifact rights basis is not ${expectedRightsBasis}.`,
    );
  }
  return {
    artifactId: metadata.id,
    sizeBytes: metadata.size_bytes,
    sha256: metadata.sha256,
    durationSeconds: metadata.duration_seconds,
    modelAssetCount: assetDigests.length,
  };
}

async function renderEspeak(client, pack, overallDeadline, voiceTimeoutMs) {
  const profileId = "acceptance-espeak-profile";
  const projectId = "acceptance-espeak-project";
  await client.request("/api/v1/voice/profiles/designed", {
    method: "POST",
    expected: [201],
    body: {
      profile_id: profileId,
      name: "Acceptance synthetic narrator",
      language: "en-US",
      description: "voice=en-us+f3; pitch=55; pace=145; amplitude=100",
      attested_by_user: true,
      notes: "Disposable synthetic voice used only in isolated release acceptance.",
    },
  });
  await client.request("/api/v1/voice/projects", {
    method: "POST",
    expected: [201],
    body: {
      project_id: projectId,
      name: "Acceptance eSpeak render",
      language: "en-US",
      default_voice_profile_id: profileId,
      blocks: [
        {
          id: "acceptance-reference",
          text: (
            "This is a disposable synthetic acceptance reference generated locally " +
            "by Project Master. It does not represent a real person."
          ),
        },
      ],
    },
  });
  const { payload: created } = await client.request("/api/v1/voice/jobs", {
    method: "POST",
    expected: [201],
    body: {
      project_id: projectId,
      engine_pack_id: pack.id,
      purpose: "private",
    },
  });
  const rendered = await runCancellableVoiceJob(
    client,
    created.id,
    voiceTimeoutMs,
    overallDeadline,
  );
  assert(
    rendered?.status === "succeeded",
    `The eSpeak voice job ended ${rendered?.status ?? "without status"}: ` +
      boundedText(
        rendered?.error ??
          rendered?.chunks?.find((chunk) => chunk?.error)?.error ??
          "no error detail",
        500,
      ),
  );
  const artifactId = rendered?.chunks?.[0]?.artifact_id;
  assert(artifactId, "The eSpeak voice job produced no artifact.");
  const { payload: metadata } = await client.request(
    `/api/v1/voice/artifacts/${encodeURIComponent(artifactId)}`,
  );
  const { content } = await client.bytes(
    `/api/v1/voice/artifacts/${encodeURIComponent(artifactId)}/content`,
    { timeoutMs: 30_000 },
  );
  const verified = validateVoiceArtifact(metadata, content, {
    engineId: "espeak-ng",
    expectedModelAssetCount: 0,
  });
  return { ...verified, content };
}

async function renderChatterbox(
  client,
  pack,
  referenceContent,
  expectedModelAssetDigests,
  overallDeadline,
  voiceTimeoutMs,
) {
  const { payload: reference } = await client.request(
    "/api/v1/voice/references",
    {
      method: "POST",
      expected: [201],
      timeoutMs: 60_000,
      body: {
        file_name: "synthetic-acceptance-reference.wav",
        audio_base64: referenceContent.toString("base64"),
        transcript: (
          "This is a disposable synthetic acceptance reference generated locally " +
          "by Project Master. It does not represent a real person."
        ),
      },
    },
  );
  const profileId = "acceptance-chatterbox-profile";
  const projectId = "acceptance-chatterbox-project";
  await client.request("/api/v1/voice/profiles/reference", {
    method: "POST",
    expected: [201],
    body: {
      profile_id: profileId,
      name: "Acceptance synthetic reference",
      language: "en-US",
      description: (
        "Disposable eSpeak-generated reference used only to exercise the " +
        "isolated Chatterbox path; it is not a real-person voice."
      ),
      reference_artifact_ids: [reference.artifact_id],
      rights_basis: "synthetic_reference",
      scopes: ["voice_generation"],
      subject_label: "Synthetic acceptance fixture (not a real person)",
      attested_by_user: true,
      evidence_artifact_ids: [],
      notes: "Generated locally by eSpeak; no real person is represented.",
    },
  });
  await client.request("/api/v1/voice/projects", {
    method: "POST",
    expected: [201],
    body: {
      project_id: projectId,
      name: "Acceptance Chatterbox render",
      language: "en-US",
      default_voice_profile_id: profileId,
      blocks: [
        {
          id: "acceptance-line",
          text: "Project Master Chatterbox acceptance passed locally.",
        },
      ],
    },
  });
  const { payload: created } = await client.request("/api/v1/voice/jobs", {
    method: "POST",
    expected: [201],
    body: {
      project_id: projectId,
      engine_pack_id: pack.id,
      purpose: "private",
    },
  });
  const rendered = await runCancellableVoiceJob(
    client,
    created.id,
    voiceTimeoutMs,
    overallDeadline,
  );
  assert(
    rendered?.status === "succeeded",
    `The Chatterbox voice job ended ${rendered?.status ?? "without status"}: ` +
      boundedText(
        rendered?.error ??
          rendered?.chunks?.find((chunk) => chunk?.error)?.error ??
          "no error detail",
        500,
      ),
  );
  const artifactId = rendered?.chunks?.[0]?.artifact_id;
  assert(artifactId, "The Chatterbox voice job produced no artifact.");
  const { payload: metadata } = await client.request(
    `/api/v1/voice/artifacts/${encodeURIComponent(artifactId)}`,
  );
  const { content } = await client.bytes(
    `/api/v1/voice/artifacts/${encodeURIComponent(artifactId)}/content`,
    { timeoutMs: 60_000 },
  );
  return validateVoiceArtifact(metadata, content, {
    engineId: "chatterbox",
    expectedModelAssetCount: 6,
    expectedModelAssetDigests,
    expectedRightsBasis: "synthetic_reference",
  });
}

export async function writeReports(report, reportBase, secrets) {
  await mkdir(path.dirname(reportBase), { recursive: true });
  const sanitized = sanitizeReportValue(report, secrets);
  const jsonDocument = `${JSON.stringify(sanitized, null, 2)}\n`;
  const markdownDocument = `${renderAcceptanceMarkdown(sanitized)}\n`;
  for (const secret of secrets) {
    assert(
      !secret ||
        (!jsonDocument.includes(secret) && !markdownDocument.includes(secret)),
      "A session token reached an acceptance report.",
    );
  }
  const jsonPath = `${reportBase}.json`;
  const markdownPath = `${reportBase}.md`;
  const nonce = `${process.pid}-${Date.now()}`;
  const tempJson = `${jsonPath}.${nonce}.tmp`;
  const tempMarkdown = `${markdownPath}.${nonce}.tmp`;
  const backupJson = `${jsonPath}.${nonce}.bak`;
  const backupMarkdown = `${markdownPath}.${nonce}.bak`;
  const documents = [
    {
      content: jsonDocument,
      path: jsonPath,
      temp: tempJson,
      backup: backupJson,
    },
    {
      content: markdownDocument,
      path: markdownPath,
      temp: tempMarkdown,
      backup: backupMarkdown,
    },
  ];
  const backedUp = [];
  const installed = [];
  try {
    await settleAll(
      documents.map((item) =>
        writeFile(item.temp, item.content, { encoding: "utf8", mode: 0o600 })
      ),
      "Acceptance report staging failed.",
    );
    for (const item of documents) {
      try {
        await rename(item.path, item.backup);
        backedUp.push(item);
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
    }
    for (const item of documents) {
      await rename(item.temp, item.path);
      installed.push(item);
    }
    await Promise.all(
      backedUp.map((item) => rm(item.backup, { force: true })),
    );
    return { jsonPath, markdownPath };
  } catch (error) {
    const rollbackErrors = [];
    for (const item of installed.reverse()) {
      await rm(item.path, { force: true }).catch((cleanupError) => {
        rollbackErrors.push(cleanupError);
      });
    }
    for (const item of backedUp.reverse()) {
      await rename(item.backup, item.path).catch((cleanupError) => {
        rollbackErrors.push(cleanupError);
      });
    }
    if (rollbackErrors.length) {
      throw new AggregateError(
        [error, ...rollbackErrors],
        "Acceptance report write failed and rollback was incomplete.",
      );
    }
    throw error;
  } finally {
    await Promise.all(
      documents.map((item) => rm(item.temp, { force: true })),
    );
  }
}

export async function testLinuxAcceptance(argv = process.argv.slice(2)) {
  if (process.platform !== "linux") {
    throw new Error("The packaged Linux acceptance gate only runs on Linux.");
  }
  const options = parseAcceptanceArguments(argv);
  const startedAt = new Date();
  const overallDeadline =
    Date.now() + options.overallTimeoutSeconds * 1_000;
  const binary = path.join(
    options.appDir,
    "usr",
    "bin",
    "project-master-backend",
  );
  const report = {
    schema_version: REPORT_SCHEMA_VERSION,
    gate: "project-master-linux-candidate",
    expected_version: EXPECTED_VERSION,
    status: "running",
    started_at: startedAt.toISOString(),
    finished_at: null,
    duration_ms: null,
    api_version: null,
    gui_launched: false,
    candidate: {
      backend: {
        relative_path: path.relative(repoRoot, binary),
        size_bytes: null,
        sha256: null,
      },
    },
    environment: {
      platform: process.platform,
      architecture: os.arch(),
      node: process.version,
      ollama_url: options.ollamaUrl,
      isolated_profile: true,
      chatterbox_assets: "copy-on-write isolated clone",
      timeouts_seconds: {
        startup: options.startupTimeoutSeconds,
        request: options.requestTimeoutSeconds,
        model: options.modelTimeoutSeconds,
        dream: options.dreamTimeoutSeconds,
        voice: options.voiceTimeoutSeconds,
        overall: options.overallTimeoutSeconds,
      },
    },
    checks: [],
    models: [],
    lifecycle: [],
    warnings: [],
    process_diagnostics: [],
  };
  const secrets = [];
  let profile;
  let activeBackend;
  let interruptedSignal;
  let phaseAStarted = false;
  let phaseAStopped = false;
  let phaseBStarted = false;
  let phaseBStopped = false;
  let projectId;
  let catalog = [];
  let toolModel;
  let chatterboxCloneReady = false;
  let chatterboxAssetDigests = [];
  let ollamaIdleForVoice = false;
  let ollamaInferenceContinuity = true;
  let reportPaths;
  const interruptionError = () =>
    interruptedSignal
      ? new Error(`Acceptance interrupted by ${interruptedSignal}.`)
      : null;
  const interruptHandlers = new Map(
    ["SIGINT", "SIGTERM"].map((signal) => [
      signal,
      () => {
        const repeated = Boolean(interruptedSignal);
        interruptedSignal ??= signal;
        const child = activeBackend?.child;
        if (child?.pid) {
          const terminationSignal = repeated ? "SIGKILL" : "SIGTERM";
          try {
            if (process.platform === "win32") {
              child.kill(terminationSignal);
            } else {
              process.kill(-child.pid, terminationSignal);
            }
          } catch (error) {
            if (error?.code !== "ESRCH") {
              console.error(
                `Unable to signal the active acceptance backend: ${error.message}`,
              );
            }
          }
        }
        console.error(
          repeated
            ? `Repeated ${signal}; forcing the active backend process group down.`
            : `${signal} received; stopping the acceptance gate safely.`,
        );
      },
    ]),
  );
  for (const [signal, handler] of interruptHandlers) {
    process.on(signal, handler);
  }
  const gate = new GateRecorder(report, secrets, interruptionError);

  try {
    const staged = await gate.run(
      "staged-appdir",
      "Exact staged AppDir backend",
      async () => {
        await verifyStagedAppDir(options.appDir);
        ensureExecutable(binary);
        const details = await lstat(binary);
        assert(
          details.isFile() && !details.isSymbolicLink() && details.size > 0,
          "Staged backend must be a nonempty regular file, not a symlink.",
        );
        const resolvedBinary = await realpath(binary);
        const relativeBinary = path.relative(
          path.resolve(options.appDir),
          resolvedBinary,
        );
        assert(
          relativeBinary &&
            !relativeBinary.startsWith("..") &&
            !path.isAbsolute(relativeBinary),
          "Staged backend resolves outside the AppDir.",
        );
        const digest = await sha256File(binary);
        report.candidate.backend.size_bytes = details.size;
        report.candidate.backend.sha256 = digest;
        return {
          detail: "Validated the staged AppDir and exact backend ELF.",
          bytes: details.size,
          sha256: digest,
        };
      },
    );
    if (!staged.ok) throw staged.error;

    const isolated = await gate.run(
      "isolated-profile",
      "Isolated database, workspace, and fixture",
      async () => {
        profile = await prepareIsolatedProfile(
          options,
          options.modelTimeoutSeconds,
        );
        return {
          detail: "Created an isolated disposable profile without a session token.",
          database: "isolated",
          workspace: "isolated",
          binder_codeword: BINDER_CODEWORD,
        };
      },
    );
    if (!isolated.ok) throw isolated.error;

    const chatterboxClone = await gate.run(
      "chatterbox-fixture",
      "Isolated Chatterbox engine fixture",
      async () => {
        const evidence = await prepareChatterboxClone(
          options.chatterboxRoot,
          profile.voiceEngine,
        );
        chatterboxCloneReady = true;
        chatterboxAssetDigests = evidence.assetDigests;
        return {
          detail: (
            "Reused the installed engine through an isolated copy-on-write " +
            "model/cache clone; no download ran."
          ),
          assets: evidence.assetCount,
          source_revision: evidence.sourceRevision,
        };
      },
    );
    if (!chatterboxClone.ok) {
      chatterboxCloneReady = false;
      chatterboxAssetDigests = [];
    }

    const port = await freePort();
    const phaseAToken = randomBytes(32).toString("hex");
    secrets.push(phaseAToken);
    const phaseA = await gate.run(
      "phase-a-startup",
      "Packaged backend phase A startup",
      async () => {
        activeBackend = await launchBackend({
          binary,
          appDir: options.appDir,
          profile,
          port,
          sessionToken: phaseAToken,
          ollamaUrl: options.ollamaUrl,
          startupTimeoutMs: requestTimeout(
            overallDeadline,
            options.startupTimeoutSeconds * 1_000,
          ),
          phase: "voice-and-foundation",
          onSpawn: (launched) => {
            activeBackend = launched;
            phaseAStarted = true;
          },
        });
        assert(
          activeBackend.ready?.version === EXPECTED_VERSION,
          `Expected API ${EXPECTED_VERSION}, received ${activeBackend.ready?.version}.`,
        );
        report.api_version = activeBackend.ready.version;
        return {
          detail: "Started the exact staged backend without launching the GUI.",
          version: activeBackend.ready.version,
        };
      },
    );
    if (!phaseA.ok) throw phaseA.error;

    let client = createApiClient({
      port,
      sessionToken: phaseAToken,
      overallDeadline,
      defaultTimeoutMs: options.requestTimeoutSeconds * 1_000,
    });

    await gate.run("authentication", "Authentication and package version", async () => {
      const missing = await client.request("/api/v1/ready", {
        token: null,
        expected: [401],
      });
      const wrong = await client.request("/api/v1/ready", {
        token: "invalid-acceptance-token",
        expected: [401],
      });
      const { payload: ready } = await client.request("/api/v1/ready");
      const { payload: schema } = await client.request("/openapi.json");
      const { payload: health } = await client.request("/api/v1/health", {
        timeoutMs: 30_000,
      });
      assert(missing.response.status === 401, "Missing token was not rejected.");
      assert(wrong.response.status === 401, "Wrong token was not rejected.");
      assert(ready?.version === EXPECTED_VERSION, "Readiness version is wrong.");
      assert(
        schema?.info?.version === EXPECTED_VERSION,
        "OpenAPI package version is wrong.",
      );
      assert(
        health?.version === EXPECTED_VERSION && health?.service === "ready",
        "Health endpoint package version/service is wrong.",
      );
      assert(health?.ok === true, "Ollama is not healthy for acceptance.");
      return {
        detail: "Missing/wrong tokens returned 401 and API/OpenAPI report 0.3.0.",
        unauthenticated_status: missing.response.status,
        wrong_token_status: wrong.response.status,
        version: ready.version,
      };
    });

    const catalogCheck = await gate.run(
      "ollama-catalog",
      "Ollama catalog and physical-model partition",
      async () => {
        const { payload: status } = await client.request(
          "/api/v1/models/status",
          { timeoutMs: 120_000 },
        );
        const directTags = await fetchLoopbackJson(
          `${options.ollamaUrl}/api/tags`,
          overallDeadline,
          30_000,
        );
        const validated = validateCatalogPartition(status, directTags);
        catalog = validated.catalog;
        toolModel = selectToolModel(catalog, status.configured_model);
        if (!toolModel) {
          report.warnings.push(
            "No inspected conversational model advertises tool support; " +
              "Binder mutation and Dream tool-model probes will be skipped.",
          );
        }
        for (const inspection of validated.inspectionErrors) {
          report.warnings.push(
            `Ollama inspection failed for ${inspection.primary_tag}: ` +
              boundedText(inspection.detail, 300),
          );
        }
        if (
          !status.models.some(
            (tag) =>
              String(tag).toLowerCase() ===
              String(status.configured_model).toLowerCase(),
          )
        ) {
          report.warnings.push(
            `Configured default ${status.configured_model} is not installed; ` +
              "manual first-run model selection remains required.",
          );
        }
        return {
          detail:
            `Accounted for ${validated.rawTagCount} tags as ` +
            `${validated.physicalModelCount} physical models.`,
          raw_tags: validated.rawTagCount,
          physical_models: validated.physicalModelCount,
          inspection_errors: validated.inspectionErrors.length,
          tool_probe_model: toolModel?.primary_tag ?? null,
        };
      },
    );
    let toolModelCheck = { ok: false };
    if (catalogCheck.ok) {
      toolModelCheck = await gate.run(
        "ollama-tool-model",
        "Tool-capable conversational model",
        async () => {
          assert(
            toolModel,
            "No inspected conversational model advertises tool support.",
          );
          return {
            detail: `Selected ${toolModel.primary_tag} for tool-policy probes.`,
            model: toolModel.primary_tag,
          };
        },
      );
    } else {
      gate.skip(
        "ollama-tool-model",
        "Tool-capable conversational model",
        "The packaged Ollama catalog failed.",
      );
    }

    const ollamaIdle = await gate.run(
      "ollama-idle",
      "Ollama idle precondition for CUDA voice",
      async () => {
      const payload = await fetchLoopbackJson(
        `${options.ollamaUrl}/api/ps`,
        overallDeadline,
        30_000,
      );
      const loaded = Array.isArray(payload?.models) ? payload.models : [];
      assert(
        loaded.length === 0,
        "Ollama already has a model loaded; stop it before the isolated CUDA voice gate.",
      );
      ollamaIdleForVoice = true;
      return {
        detail: "No Ollama model was resident before Chatterbox loaded CUDA.",
        loaded_models: 0,
      };
      },
    );
    if (!ollamaIdle.ok) throw ollamaIdle.error;

    await gate.run("tool-diagnostics", "Safe tool inventory and diagnostics", async () => {
      const { payload } = await client.request("/api/v1/tools/status");
      assert(
        payload?.default_chat_policy === "read_only",
        "Default chat policy is not read-only.",
      );
      assert(
        payload?.mutating_tools_require_explicit_chat_authorization === true,
        "Mutating tools do not require explicit chat authorization.",
      );
      assert(
        payload?.workspace_writes_enabled === true,
        "Acceptance workspace writes are not configured.",
      );
      assert(
        payload?.terminal?.enabled === true &&
          payload?.terminal?.network_enabled === false,
        "Terminal/network policy is not enabled-local and network-disabled.",
      );
      const inventory = new Map(
        (payload?.tools ?? []).map((item) => [item.name, item]),
      );
      for (const name of EXPECTED_TOOL_NAMES) {
        assert(inventory.has(name), `Expected tool is missing: ${name}`);
      }
      for (const item of inventory.values()) {
        assert(
          item.risk === (item.mutating ? "mutating" : "read_only"),
          `Tool risk metadata is inconsistent: ${item.name}`,
        );
        assert(
          item.available_in_default_chat === !item.mutating,
          `Default availability is inconsistent: ${item.name}`,
        );
      }
      for (const [name, diagnostic] of Object.entries(
        payload?.diagnostics ?? {},
      )) {
        assert(diagnostic?.ok === true, `Safe diagnostic failed: ${name}`);
      }
      assert(
        Object.keys(payload?.diagnostics ?? {}).sort().join(",") ===
          "calculator,current_time,workspace_list",
        "Tool status ran an unexpected diagnostic set.",
      );
      const calculator = JSON.parse(payload.diagnostics.calculator.result);
      assert(calculator.result === 42, "Calculator diagnostic did not return 42.");
      return {
        detail: (
          `Validated ${inventory.size} tools and all three safe diagnostics; ` +
          `terminal sandbox=${payload.terminal.sandbox}.`
        ),
        tools: inventory.size,
        diagnostics: 3,
        terminal_sandbox: payload.terminal.sandbox,
      };
    });

    const binder = await gate.run(
      "binder-index-search",
      "Binder indexing, exclusions, search, and citation",
      async () => {
        const { payload: project } = await client.request("/api/v1/projects", {
          method: "POST",
          expected: [201],
          body: {
            name: "Packaged acceptance",
            root_path: profile.project,
            description: "Disposable packaged acceptance project.",
          },
        });
        projectId = project.id;
        const { payload: indexed } = await client.request(
          `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/index`,
          {
            method: "POST",
            body: { relative_path: ".", prune: true },
            timeoutMs: 60_000,
          },
        );
        assert(indexed?.indexed === 1, "Binder did not index exactly one safe fixture.");
        assert(
          indexed?.errors?.length === 0,
          "Binder reported fixture indexing errors.",
        );
        const { payload: searched } = await client.request(
          `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/search?` +
            `query=${encodeURIComponent(BINDER_CODEWORD)}&limit=8`,
        );
        const hit = searched?.results?.find((item) =>
          String(item.content).includes(BINDER_CODEWORD)
        );
        assert(hit, "Binder search did not return the exact codeword.");
        assert(
          /^ACCEPTANCE\.md:\d+(?:-\d+)?$/.test(hit.citation),
          "Binder search did not return a line citation.",
        );
        assert(
          hit.document_version === 1 && /^[0-9a-f]{64}$/.test(hit.content_sha256),
          "Binder result is missing version/SHA provenance.",
        );
        const { payload: secretSearch } = await client.request(
          `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/search?` +
            `query=${encodeURIComponent("ACCEPTANCE_SECRET_MUST_NOT_BE_INDEXED")}&limit=8`,
        );
        assert(
          secretSearch?.results?.length === 0,
          "Binder indexed the excluded .env fixture.",
        );
        return {
          detail: `Indexed one safe file and retrieved ${hit.citation}.`,
          indexed: indexed.indexed,
          citation: hit.citation,
          document_version: hit.document_version,
          content_sha256: hit.content_sha256,
          secret_excluded: true,
        };
      },
    );
    if (!binder.ok) projectId = undefined;

    await gate.run("dream-controls", "Model-free Dream schedule controls", async () => {
      const { payload: overview } = await client.request("/api/v1/dreams");
      assert(overview?.proposal_only === true, "Dream Lab is not proposal-only.");
      assert(
        overview?.scheduled_execution_enabled === true,
        "Dream scheduler did not start with the backend.",
      );
      const scheduleId = "acceptance-disabled";
      const { payload: created } = await client.request(
        "/api/v1/dreams/schedules",
        {
          method: "POST",
          expected: [201],
          body: {
            schedule_id: scheduleId,
            recipe_id: "idea-garden",
            timezone: "UTC",
            local_time: "23:59:59",
            enabled: false,
            expected_version: 0,
            resource_rules: {
              min_idle_seconds: 600,
              require_no_model_jobs: true,
            },
          },
        },
      );
      assert(created?.enabled === false, "Dream fixture was unexpectedly enabled.");
      const enableAttempt = await client.request(
        `/api/v1/dreams/schedules/${scheduleId}/enabled`,
        {
          method: "POST",
          body: { enabled: true },
          expected: [422],
        },
      );
      assert(
        enableAttempt.response.status === 422,
        "Unscoped Dream fixture was unexpectedly enabled.",
      );
      const { payload: stillDisabled } = await client.request(
        `/api/v1/dreams/schedules/${scheduleId}`,
      );
      assert(
        stillDisabled?.enabled === false,
        "Rejected Dream enable changed persisted state.",
      );
      await client.request(`/api/v1/dreams/schedules/${scheduleId}`, {
        method: "DELETE",
        expected: [204],
      });
      const { payload: listed } = await client.request(
        "/api/v1/dreams/schedules",
      );
      assert(
        !listed?.schedules?.some((item) => item.schedule_id === scheduleId),
        "Deleted Dream schedule remains listed.",
      );
      return {
        detail: "Created a disabled schedule, rejected unsafe enable, and deleted it.",
        proposal_only: true,
        unsafe_enable_status: enableAttempt.response.status,
      };
    });

    await gate.run("comfy-offline", "Supported ComfyUI-offline behavior", async () => {
      const closedPort = await freePort();
      const profileId = "acceptance-offline";
      await client.request("/api/v1/integrations/comfyui/profiles", {
        method: "POST",
        body: {
          id: profileId,
          name: "Acceptance offline ComfyUI",
          base_url: `http://127.0.0.1:${closedPort}`,
          timeout_seconds: 1,
        },
      });
      const { payload: overview } = await client.request(
        "/api/v1/integrations/comfyui",
      );
      const { payload: status } = await client.request(
        `/api/v1/integrations/comfyui/profiles/${profileId}/status`,
        { timeoutMs: 10_000 },
      );
      assert(
        overview?.support_available === true,
        "ComfyUI support disappeared while offline.",
      );
      assert(
        status?.profile_id === profileId && status?.ok === false,
        "ComfyUI offline state was not reported as HTTP 200/ok=false.",
      );
      const { payload: ready } = await client.request("/api/v1/ready");
      assert(ready?.ok === true, "ComfyUI offline state disrupted backend readiness.");
      return {
        detail: "Offline profile returned HTTP 200/ok=false without blocking startup.",
        profile_id: profileId,
        ok: status.ok,
      };
    });

    let voiceOverview;
    const voiceHealth = await gate.run(
      "voice-health",
      "eSpeak and Chatterbox engine health",
      async () => {
        assert(chatterboxCloneReady, "The isolated Chatterbox fixture is unavailable.");
        assert(ollamaIdleForVoice, "Ollama was not idle before the CUDA voice phase.");
        const { payload } = await client.request("/api/v1/voice");
        voiceOverview = payload;
        const espeak = payload?.installed_packs?.find(
          (item) => item.engine_id === "espeak-ng",
        );
        const chatterbox = payload?.installed_packs?.find(
          (item) => item.engine_id === "chatterbox",
        );
        assert(espeak, "The packaged runtime did not discover eSpeak NG.");
        assert(chatterbox, "The packaged runtime did not discover Chatterbox.");
        assert(
          chatterbox.assets?.length === 6,
          "Chatterbox did not register all six pinned assets.",
        );
        const { payload: espeakStatus } = await client.request(
          `/api/v1/voice/engines/${encodeURIComponent(espeak.id)}/health`,
          { timeoutMs: 60_000 },
        );
        const { payload: chatterboxStatus } = await client.request(
          `/api/v1/voice/engines/${encodeURIComponent(chatterbox.id)}/health`,
          { timeoutMs: 60_000 },
        );
        assert(
          espeakStatus?.available === true && espeakStatus?.status === "ready",
          `eSpeak is not ready: ${espeakStatus?.detail ?? "unknown"}`,
        );
        assert(
          chatterboxStatus?.available === true &&
            chatterboxStatus?.status === "ready",
          `Chatterbox is not ready: ${chatterboxStatus?.detail ?? "unknown"}`,
        );
        assert(
          /\bon cuda\b/i.test(chatterboxStatus.detail),
          "Chatterbox is not ready on CUDA as claimed for this candidate.",
        );
        return {
          evidence: {
            detail: "eSpeak is ready and Chatterbox is ready on CUDA with six assets.",
            espeak: espeakStatus.detail,
            chatterbox: chatterboxStatus.detail,
            chatterbox_assets: chatterbox.assets.length,
          },
          value: { espeak, chatterbox },
        };
      },
    );

    let espeakRender;
    if (voiceHealth.ok) {
      const espeakCheck = await gate.run(
        "espeak-render",
        "eSpeak isolated render and verified artifact",
        async () => {
          espeakRender = await renderEspeak(
            client,
            voiceHealth.value.espeak,
            overallDeadline,
            Math.min(options.voiceTimeoutSeconds * 1_000, 180_000),
          );
          return {
            detail: "Rendered and checksum-verified an isolated eSpeak WAV.",
            artifact_id: espeakRender.artifactId,
            bytes: espeakRender.sizeBytes,
            sha256: espeakRender.sha256,
          };
        },
      );
      if (espeakCheck.ok) {
        await gate.run(
          "chatterbox-render",
          "Chatterbox offline CUDA render and verified artifact",
          async () => {
            const rendered = await renderChatterbox(
              client,
              voiceHealth.value.chatterbox,
              espeakRender.content,
              chatterboxAssetDigests,
              overallDeadline,
              options.voiceTimeoutSeconds * 1_000,
            );
            return {
              detail: (
                "Rendered and checksum-verified a Chatterbox WAV without " +
                "downloading; the reference was a disposable synthetic fixture."
              ),
              artifact_id: rendered.artifactId,
              bytes: rendered.sizeBytes,
              sha256: rendered.sha256,
              model_asset_digests: rendered.modelAssetCount,
              rights_basis: "synthetic_reference",
            };
          },
        );
      } else {
        gate.skip(
          "chatterbox-render",
          "Chatterbox offline CUDA render and verified artifact",
          "The synthetic eSpeak reference fixture was unavailable.",
        );
      }
    } else {
      gate.skip(
        "espeak-render",
        "eSpeak isolated render and verified artifact",
        "Voice engine health failed.",
      );
      gate.skip(
        "chatterbox-render",
        "Chatterbox offline CUDA render and verified artifact",
        "Voice engine health failed.",
      );
    }

    const phaseAStop = await gate.run(
      "phase-a-shutdown",
      "Phase A shutdown and port release",
      async () => {
        const outcome = await stopBackend(activeBackend);
        report.lifecycle.push(outcome);
        activeBackend = null;
        phaseAStopped = true;
        return {
          detail: `Stopped the voice worker/backend with ${outcome.signal}; port closed.`,
          ...outcome,
        };
      },
    );
    if (!phaseAStop.ok) throw phaseAStop.error;

    const phaseBToken = randomBytes(32).toString("hex");
    secrets.push(phaseBToken);
    const phaseB = await gate.run(
      "phase-b-relaunch",
      "Packaged backend phase B relaunch",
      async () => {
        activeBackend = await launchBackend({
          binary,
          appDir: options.appDir,
          profile,
          port,
          sessionToken: phaseBToken,
          ollamaUrl: options.ollamaUrl,
          startupTimeoutMs: requestTimeout(
            overallDeadline,
            options.startupTimeoutSeconds * 1_000,
          ),
          phase: "ollama-and-council",
          onSpawn: (launched) => {
            activeBackend = launched;
            phaseBStarted = true;
          },
        });
        return {
          detail: "Relaunched the same isolated profile with a new session token.",
          version: activeBackend.ready.version,
        };
      },
    );
    if (!phaseB.ok) throw phaseB.error;

    client = createApiClient({
      port,
      sessionToken: phaseBToken,
      overallDeadline,
      defaultTimeoutMs: options.requestTimeoutSeconds * 1_000,
    });

    await gate.run("token-rotation", "Per-launch token rotation", async () => {
      const oldToken = await client.request("/api/v1/ready", {
        token: phaseAToken,
        expected: [401],
      });
      const { payload: ready } = await client.request("/api/v1/ready");
      assert(oldToken.response.status === 401, "Phase A token remained valid.");
      assert(ready?.version === EXPECTED_VERSION, "Phase B token did not authenticate.");
      return {
        detail: "The old launch token returned 401 and the new token authenticated.",
        old_token_status: oldToken.response.status,
      };
    });

    await gate.run("restart-persistence", "Isolated state persisted across relaunch", async () => {
      assert(projectId, "The Binder project was not created in phase A.");
      const { payload: project } = await client.request(
        `/api/v1/projects/${encodeURIComponent(projectId)}`,
      );
      const { payload: knowledge } = await client.request(
        `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge`,
      );
      const { payload: voice } = await client.request("/api/v1/voice");
      assert(project?.id === projectId, "Project did not persist across restart.");
      assert(
        knowledge?.documents?.some(
          (item) => item.relative_path === "ACCEPTANCE.md",
        ),
        "Binder index did not persist across restart.",
      );
      assert(
        voice?.artifacts?.length >= 2,
        "Voice artifacts did not persist across restart.",
      );
      return {
        detail: "Project, Binder index, and both voice artifacts survived relaunch.",
        binder_documents: knowledge.documents.length,
        voice_artifacts: voice.artifacts.length,
      };
    });

    if (catalogCheck.ok && projectId && toolModelCheck.ok) {
      await gate.run("binder-chat", "Binder context through packaged Direct chat", async () => {
        const result = await streamChat(
          client,
          {
            sessionToken: phaseBToken,
            message: (
              "What is the release-candidate verification phrase in this project? " +
              "Reply with only the phrase."
            ),
            model: toolModel.primary_tag,
            mode: "direct",
            project_id: projectId,
            allow_mutations: false,
            request_id: "accept-binder-chat",
          },
          {
            timeoutMs: options.modelTimeoutSeconds * 1_000,
            overallDeadline,
          },
        );
        assert(
          result.content.includes(BINDER_CODEWORD),
          "Direct Binder chat did not return the exact codeword.",
        );
        return {
          detail: "Direct chat returned the exact codeword from bounded Binder context.",
          model: toolModel.primary_tag,
          codeword_returned: true,
        };
      });

      await gate.run("mutation-denied", "Default workspace mutation remains read-only", async () => {
        const deniedPath = path.join(profile.project, "acceptance-denied.txt");
        const result = await streamChat(
          client,
          {
            sessionToken: phaseBToken,
            message: (
              "Attempt to use workspace_write to create acceptance-denied.txt " +
              "containing DENIED. Do not use any other tool and do not claim success " +
              "without a successful tool result."
            ),
            model: toolModel.primary_tag,
            mode: "direct",
            project_id: projectId,
            allow_mutations: false,
            request_id: "accept-mutation-denied",
          },
          {
            timeoutMs: options.modelTimeoutSeconds * 1_000,
            overallDeadline,
          },
        );
        const deniedExists = await lstat(deniedPath)
          .then(() => true)
          .catch(() => false);
        assert(!deniedExists, "Read-only chat created the denied mutation file.");
        assert(
          result.start?.tool_authorization === "read_only",
          "Denied mutation stream did not report read-only authorization.",
        );
        const writeAttempts = result.tools.filter(
          (tool) => tool.name === "workspace_write",
        );
        assert(
          writeAttempts.every((tool) => tool.ok === false),
          "Read-only chat reported a successful workspace_write.",
        );
        return {
          detail: writeAttempts.length
            ? "Read-only chat rejected the write attempt and the file is absent."
            : "Read-only chat hid the mutating schema and the file is absent.",
          file_absent: true,
          authorization: result.start.tool_authorization,
          enforcement:
            writeAttempts.length ? "execution_rejected" : "schema_hidden",
        };
      });

      await gate.run(
        "mutation-authorized",
        "Explicitly authorized workspace mutation",
        async () => {
          const authorizedPath = path.join(
            profile.project,
            "acceptance-authorized.txt",
          );
          const result = await streamChat(
            client,
            {
              sessionToken: phaseBToken,
              message: (
                "Use workspace_write exactly once to create acceptance-authorized.txt " +
                `with these exact ${Buffer.byteLength(AUTHORIZED_MUTATION_CONTENT)} ASCII ` +
                `bytes: ${AUTHORIZED_MUTATION_CONTENT}. Do not add quotes or a trailing newline. ` +
                "Do not use another tool. Report success only after the tool succeeds."
              ),
              model: toolModel.primary_tag,
              mode: "direct",
              project_id: projectId,
              allow_mutations: true,
              request_id: "accept-mutation-authorized",
            },
            {
              timeoutMs: options.modelTimeoutSeconds * 1_000,
              overallDeadline,
            },
          );
          const writes = result.tools.filter(
            (tool) => tool.name === "workspace_write",
          );
          assert(
            result.tools.length === 1 && writes.length === 1 && writes[0].ok === true,
            "Authorized chat did not execute exactly one successful workspace_write.",
          );
          assert(
            result.start?.tool_authorization === "explicit_mutations_allowed",
            "Authorized stream did not report explicit mutation permission.",
          );
          const argumentContent = writes[0].arguments?.content;
          assert(
            argumentContent === AUTHORIZED_MUTATION_CONTENT,
            (
              "Authorized workspace_write content argument is not exact " +
              `(expected_bytes=${Buffer.byteLength(AUTHORIZED_MUTATION_CONTENT)}, ` +
              `actual_bytes=${
                typeof argumentContent === "string"
                  ? Buffer.byteLength(argumentContent)
                  : "non_string"
              }, expected_sha256=${sha256Bytes(AUTHORIZED_MUTATION_CONTENT)}, ` +
              `actual_sha256=${
                typeof argumentContent === "string"
                  ? sha256Bytes(argumentContent)
                  : "non_string"
              }).`
            ),
          );
          const content = await readFile(authorizedPath, "utf8");
          assert(
            content === AUTHORIZED_MUTATION_CONTENT,
            (
              "Authorized workspace file content is not exact " +
              `(expected_bytes=${Buffer.byteLength(AUTHORIZED_MUTATION_CONTENT)}, ` +
              `actual_bytes=${Buffer.byteLength(content)}, ` +
              `expected_sha256=${sha256Bytes(AUTHORIZED_MUTATION_CONTENT)}, ` +
              `actual_sha256=${sha256Bytes(content)}).`
            ),
          );
          return {
            detail: "Explicit authorization produced one verified workspace write.",
            model: toolModel.primary_tag,
            path: "acceptance-authorized.txt",
            bytes: Buffer.byteLength(content),
            tool_calls: result.tools.length,
          };
        },
      );

    } else {
      for (const [id, title] of [
        ["binder-chat", "Binder context through packaged Direct chat"],
        ["mutation-denied", "Default workspace mutation remains read-only"],
        ["mutation-authorized", "Explicitly authorized workspace mutation"],
      ]) {
        gate.skip(id, title, "The Binder fixture or tool-capable model failed.");
      }
    }

    if (catalogCheck.ok) {
      await gate.run(
        "physical-model-smoke",
        "Every physical Ollama model accounted for once",
        async () => {
          report.models = [];
          const harnessModelTags = sortedUnique(
            catalog.flatMap((model) => model.tags),
          );
          const baseline = await unloadHarnessOllamaModels(
            options.ollamaUrl,
            harnessModelTags,
          );
          assert(
            baseline.foreign.length === 0,
            (
              "A model owned by another local client was resident before the physical " +
              `model gate: ${baseline.foreign.join(", ")}`
            ),
          );
          for (let index = 0; index < catalog.length; index += 1) {
            const model = catalog[index];
            const started = Date.now();
            const record = {
              physical_id: model.physical_id,
              primary_tag: model.primary_tag,
              aliases: [...model.tags],
              digest: model.digest,
              capabilities: [...(model.capabilities ?? [])],
              status: "failed",
              duration_ms: 0,
              detail: "",
              residency_cleanup: null,
            };
            if (model.inspection_error) {
              record.status = "failed";
              record.detail =
                `Ollama inspection failed: ${boundedText(model.inspection_error, 500)}`;
              report.models.push(record);
              console.error(`[FAIL] model ${model.primary_tag}: ${record.detail}`);
              continue;
            }
            if (!catalogModelSupportsCompletion(model)) {
              record.status = "skipped";
              record.detail = "Explicitly ineligible: model has no completion capability.";
              report.models.push(record);
              console.log(`[SKIP] model ${model.primary_tag}: non-completion`);
              continue;
            }
            let cleanupFailure = null;
            try {
              const result = await streamChat(
                client,
                {
                  sessionToken: phaseBToken,
                  message: (
                    `Reply with the exact token ${MODEL_RESPONSE_CODEWORD} ` +
                    "and no additional words."
                  ),
                  model: model.primary_tag,
                  mode: "direct",
                  allow_mutations: false,
                  request_id: `accept-model-${String(index + 1).padStart(2, "0")}`,
                },
                {
                  timeoutMs: options.modelTimeoutSeconds * 1_000,
                  overallDeadline,
                },
              );
              assert(
                result.content.includes(MODEL_RESPONSE_CODEWORD),
                "Model response omitted the acceptance token.",
              );
              record.status = "passed";
              record.detail = "Returned a completed Direct response.";
              console.log(`[PASS] model ${model.primary_tag}`);
            } catch (error) {
              record.status = "failed";
              record.detail = errorMessage(error, secrets);
              console.error(`[FAIL] model ${model.primary_tag}: ${record.detail}`);
            } finally {
              try {
                const cleanup = await unloadHarnessOllamaModels(
                  options.ollamaUrl,
                  harnessModelTags,
                );
                assert(
                  cleanup.foreign.length === 0,
                  (
                    "A model owned by another local client appeared during the physical " +
                    `model gate: ${cleanup.foreign.join(", ")}`
                  ),
                );
                record.residency_cleanup = {
                  unloaded_models: cleanup.unloaded,
                  foreign_models: cleanup.foreign,
                  harness_models_resident_after: 0,
                };
              } catch (error) {
                ollamaInferenceContinuity = false;
                cleanupFailure = error;
                const detail = errorMessage(error, secrets);
                record.detail = record.detail
                  ? `${record.detail}; residency cleanup failed: ${detail}`
                  : `Residency cleanup failed: ${detail}`;
                record.status = "failed";
              }
              record.duration_ms = Date.now() - started;
              report.models.push(record);
            }
            if (cleanupFailure) {
              throw new Error(
                `Ollama isolation failed after ${model.primary_tag}: ` +
                  errorMessage(cleanupFailure, secrets),
              );
            }
          }
          const ids = report.models.map((item) => item.physical_id);
          assert(
            ids.length === catalog.length && new Set(ids).size === catalog.length,
            "Physical model report entries are missing or duplicated.",
          );
          const failed = report.models.filter((item) => item.status === "failed");
          assert(
            failed.length === 0,
            `${failed.length} conversational physical model(s) failed acceptance.`,
          );
          return {
            detail:
              `Recorded ${report.models.length} physical models; ` +
              `${report.models.filter((item) => item.status === "skipped").length} ` +
              "intentional non-completion skip(s).",
            passed: report.models.filter((item) => item.status === "passed").length,
            skipped: report.models.filter((item) => item.status === "skipped").length,
          };
        },
      );
    } else {
      gate.skip(
        "physical-model-smoke",
        "Every physical Ollama model accounted for once",
        "The packaged Ollama catalog failed.",
      );
    }

    if (
      catalogCheck.ok &&
      toolModelCheck.ok &&
      ollamaInferenceContinuity
    ) {
      await gate.run("dream-proposal", "Dream council proposal remains pending", async () => {
        const requestId = "acceptance-dream-council";
        const { payload: queued } = await client.request(
          "/api/v1/dreams/runs/manual",
          {
            method: "POST",
            expected: [202],
            body: {
              recipe_id: "idea-garden",
              request_id: requestId,
              preferred_lead: toolModel.primary_tag,
              sources: [
                {
                  source_id: "acceptance-note",
                  kind: "user_note",
                  locator: "acceptance://isolated-note",
                  content: (
                    "Propose one small, reversible way to improve the packaged " +
                    "release acceptance workflow. Treat it as speculation only."
                  ),
                  sensitivity: "internal",
                  allow_dreaming: true,
                },
              ],
            },
          },
        );
        const runId = queued?.run?.run_id;
        assert(runId, "Dream API did not return a queued run ID.");
        const deadline = Date.now() +
          requestTimeout(
            overallDeadline,
            options.dreamTimeoutSeconds * 1_000,
          );
        let run;
        while (Date.now() < deadline) {
          const response = await client.request(
            `/api/v1/dreams/runs/${encodeURIComponent(runId)}`,
            { timeoutMs: 15_000 },
          );
          run = response.payload;
          if (TERMINAL_DREAM_STATUSES.has(run?.status)) break;
          await new Promise((resolve) => setTimeout(resolve, 1_000));
        }
        if (!run || !TERMINAL_DREAM_STATUSES.has(run.status)) {
          await client.request(
            `/api/v1/dreams/runs/${encodeURIComponent(runId)}/cancel`,
            { method: "POST", timeoutMs: 10_000 },
          ).catch(() => null);
          throw new Error(
            `Dream council exceeded its ${options.dreamTimeoutSeconds}s timeout.`,
          );
        }
        assert(
          ["complete", "partial"].includes(run.status),
          `Dream council ended ${run.status}: ` +
            boundedText(run.error ?? "no error detail", 700),
        );
        assert(run.item_id, `${run.status} Dream did not create a proposal item.`);
        const { payload: item } = await client.request(
          `/api/v1/dreams/inbox/${encodeURIComponent(run.item_id)}`,
        );
        assert(item?.disposition === "pending", "Dream proposal is not pending.");
        assert(
          item?.epistemic_label === "speculation",
          "Dream proposal is not labeled speculation.",
        );
        assert(
          typeof item?.proposal_text === "string" && item.proposal_text.trim(),
          "Dream proposal text is empty.",
        );
        return {
          detail:
            `Council ended ${run.status} and produced a pending ` +
            "speculation-only proposal.",
          run_id: runId,
          run_status: run.status,
          item_id: item.item_id,
          disposition: item.disposition,
          epistemic_label: item.epistemic_label,
        };
      });
    } else {
      gate.skip(
        "dream-proposal",
        "Dream council proposal remains pending",
        ollamaInferenceContinuity
          ? "The packaged Ollama catalog or tool-capable model failed."
          : "Ollama inference continuity was lost during the physical-model gate.",
      );
    }

    await gate.run("database-created", "Isolated database creation", async () => {
      const details = await stat(profile.dbPath);
      assert(details.isFile() && details.size > 0, "Backend database is missing.");
      return {
        detail: "The packaged backend created a nonempty isolated SQLite database.",
        nonempty: true,
      };
    });

    await gate.run("phase-b-shutdown", "Final shutdown and port release", async () => {
      const outcome = await stopBackend(activeBackend);
      report.lifecycle.push(outcome);
      activeBackend = null;
      phaseBStopped = true;
      return {
        detail: `Stopped the final backend with ${outcome.signal}; port closed.`,
        ...outcome,
      };
    });
  } catch (error) {
    report.checks.push({
      id: "gate-fatal",
      title: "Gate orchestration",
      status: "failed",
      duration_ms: 0,
      detail: errorMessage(error, secrets),
      evidence: {},
    });
  } finally {
    if (activeBackend) {
      const backend = activeBackend;
      const diagnostics = await backend.diagnostics().catch((error) => ({
        phase: backend.phase,
        diagnostics_error: errorMessage(error, secrets),
      }));
      report.process_diagnostics.push(diagnostics);
      try {
        const outcome = await stopBackend(backend);
        report.lifecycle.push(outcome);
        if (backend.phase === "voice-and-foundation") phaseAStopped = true;
        if (backend.phase === "ollama-and-council") phaseBStopped = true;
        activeBackend = null;
      } catch (error) {
        report.checks.push({
          id: "emergency-shutdown",
          title: "Emergency backend shutdown",
          status: "failed",
          duration_ms: 0,
          detail: errorMessage(error, secrets),
          evidence: {},
        });
      }
    }
    if (ollamaIdleForVoice) {
      const cleanupStarted = Date.now();
      try {
        const { unloaded, foreign } = await unloadHarnessOllamaModels(
          options.ollamaUrl,
          catalog.flatMap((model) => model.tags),
        );
        report.checks.push({
          id: "ollama-cleanup",
          title: "Ollama model unload",
          status: foreign.length ? "failed" : "passed",
          duration_ms: Date.now() - cleanupStarted,
          detail: foreign.length
            ? (
              "A model loaded by another local client appeared during the gate; " +
              `it was left untouched: ${foreign.join(", ")}`
            )
            : unloaded.length
              ? `Unloaded ${unloaded.length} harness-loaded Ollama model(s).`
              : "Ollama remained idle.",
          evidence: {
            unloaded_models: unloaded,
            foreign_models_left_untouched: foreign,
            harness_models_resident_after: 0,
          },
        });
      } catch (error) {
        report.checks.push({
          id: "ollama-cleanup",
          title: "Ollama model unload",
          status: "failed",
          duration_ms: Date.now() - cleanupStarted,
          detail: errorMessage(error, secrets),
          evidence: {},
        });
      }
    }
    if (profile && !activeBackend) {
      try {
        assert(
          await safeAcceptanceRoot(profile.root, profile.parent),
          `Refusing to remove unexpected acceptance root: ${profile.root}`,
        );
        await rm(profile.root, { recursive: true, force: true });
        report.environment.isolated_profile_removed = true;
      } catch (error) {
        report.environment.isolated_profile_retained =
          path.relative(repoRoot, profile.root);
        report.checks.push({
          id: "fixture-cleanup",
          title: "Isolated fixture cleanup",
          status: "failed",
          duration_ms: 0,
          detail: errorMessage(error, secrets),
          evidence: {},
        });
      }
    } else if (profile) {
      report.environment.isolated_profile_retained =
        path.relative(repoRoot, profile.root);
      report.checks.push({
        id: "fixture-retained",
        title: "Isolated fixture retained after incomplete shutdown",
        status: "failed",
        duration_ms: 0,
        detail: (
          "The backend process tree or port was not confirmed closed; " +
          `the fixture was preserved at ${profile.root}.`
        ),
        evidence: {},
      });
      console.error(
        `Acceptance fixture preserved after incomplete shutdown: ${profile.root}`,
      );
    }
    for (const [signal, handler] of interruptHandlers) {
      process.off(signal, handler);
    }
    if (interruptedSignal) {
      report.checks.push({
        id: "gate-interrupted",
        title: "Acceptance interruption",
        status: "failed",
        duration_ms: 0,
        detail: `Acceptance was interrupted by ${interruptedSignal}.`,
        evidence: {},
      });
    }
    if (phaseAStarted && !phaseAStopped) {
      report.checks.push({
        id: "phase-a-lifecycle-incomplete",
        title: "Phase A lifecycle completeness",
        status: "failed",
        duration_ms: 0,
        detail: "Phase A started but its port-close result was not confirmed.",
        evidence: {},
      });
    }
    if (phaseBStarted && !phaseBStopped) {
      report.checks.push({
        id: "phase-b-lifecycle-incomplete",
        title: "Phase B lifecycle completeness",
        status: "failed",
        duration_ms: 0,
        detail: "Phase B started but its port-close result was not confirmed.",
        evidence: {},
      });
    }
    const failedChecks = report.checks.filter(
      (item) => item.status === "failed",
    );
    const failedModels = report.models.filter(
      (item) => item.status === "failed",
    );
    report.status =
      failedChecks.length || failedModels.length ? "failed" : "passed";
    const finishedAt = new Date();
    report.finished_at = finishedAt.toISOString();
    report.duration_ms = finishedAt.getTime() - startedAt.getTime();
    reportPaths = await writeReports(report, options.reportBase, secrets);
  }

  console.log(`Acceptance JSON: ${reportPaths.jsonPath}`);
  console.log(`Acceptance Markdown: ${reportPaths.markdownPath}`);
  if (report.status !== "passed") {
    throw new Error(
      "Packaged Linux acceptance failed. See the generated report for details.",
    );
  }
  console.log("Packaged Linux acceptance passed with the desktop GUI closed.");
  return report;
}

if (isMain(import.meta.url)) {
  testLinuxAcceptance().catch((error) => {
    console.error(`Linux acceptance failed: ${error.message}`);
    process.exitCode = 1;
  });
}
