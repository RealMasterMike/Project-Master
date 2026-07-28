import { invoke } from "@tauri-apps/api/core";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

export const API_BASE_URL = "http://127.0.0.1:8765/api/v1";
export const API_UNREACHABLE_MESSAGE =
  "Project Master backend is not reachable at 127.0.0.1:8765 — is it running?";

const STATUS_TIMEOUT_MS = 8_000;
let desktopSessionToken: string | null = null;

export interface ProjectMasterModel {
  name: string;
  capabilities: string[];
  conversational: boolean;
  toolCapable: boolean;
}

export type ProjectMasterChatMode = "direct" | "team";

export interface ProjectMasterTeamCatalogModel {
  physicalId: string;
  primaryTag: string;
  tags: string[];
  capabilities: string[];
  sizeBytes: number;
}

export interface ProjectMasterRunActivity {
  kind: string;
  message: string;
  runId?: string;
  model?: string;
  role?: string;
  tool?: string;
  ok?: boolean;
  inputDetail?: string;
  outputDetail?: string;
}

export interface MasterProject {
  id: string;
  name: string;
  description: string;
  status: string;
  rootPath?: string;
  allowDreaming: boolean;
  updatedAt: string;
}

export interface MasterRun {
  id: string;
  projectId: string;
  kind: string;
  objective: string;
  mode: string;
  status: string;
  createdAt: string;
  completedAt?: string;
}

export interface MasterRunEvent {
  id: number;
  type: string;
  summary: string;
  createdAt: string;
}

export interface KnowledgeDocumentSummary {
  id: string;
  projectId: string;
  relativePath: string;
  sha256: string;
  version: number;
  mimeType: string;
  sizeBytes: number;
  indexedAt: string;
  active: boolean;
}

export interface KnowledgeSearchHit {
  documentId: string;
  relativePath: string;
  citation: string;
  excerpt: string;
  score: number;
  sha256: string;
  documentVersion: number;
}

export interface KnowledgeIndexSummary {
  projectId: string;
  indexed: number;
  unchanged: number;
  skipped: number;
  archived: number;
  errorCount: number;
}

export interface MasterApproval {
  id: string;
  runId: string;
  actionKind: string;
  target: string;
  risk: string;
  reversible: boolean;
  rollbackPlan: string;
  status: string;
  createdAt: string;
}

export interface DreamRecipeSummary {
  recipeId: string;
  name: string;
  objective: string;
  kind: string;
  sourceScopes: string[];
  version: number;
}

export interface DreamRunSummary {
  runId: string;
  recipeId: string;
  status: string;
  createdAt: string;
  itemId?: string;
  error?: string;
}

export interface DreamInboxItem {
  itemId: string;
  recipeId: string;
  proposalText: string;
  epistemicLabel: string;
  sourceRefs: string[];
  disposition: string;
  createdAt: string;
}

export interface DreamOverview {
  proposalOnly: boolean;
  scheduledExecutionEnabled: boolean;
  backgroundConfigured: boolean;
  recipes: DreamRecipeSummary[];
  schedules: DreamScheduleSummary[];
  runs: DreamRunSummary[];
  inbox: DreamInboxItem[];
}

export interface DreamResourceRules {
  minIdleSeconds: number;
  maxCpuPercent: number;
  minAvailableMemoryBytes: number;
  minGpuFreeBytes?: number;
  requireNoModelJobs: boolean;
  requireAcPower: boolean;
}

export interface DreamQuietWindow {
  timezone: string;
  startLocal: string;
  endLocal: string;
  weekdays: number[];
}

export interface DreamScheduleSummary {
  scheduleId: string;
  recipeId: string;
  timezone: string;
  localTime: string;
  enabled: boolean;
  catchUp: "skip" | "latest" | "all_bounded";
  onTimeGraceSeconds: number;
  maxLookbackDays: number;
  maxCatchUpWindows: number;
  resourceRules: DreamResourceRules;
  quietWindow?: DreamQuietWindow;
  version: number;
  updatedAt: string;
}

export interface ComfyProfileSummary {
  id: string;
  name: string;
  baseUrl: string;
  verifyTls: boolean;
}

export interface ComfyWorkflowSummary {
  id: string;
  name: string;
  digest: string;
  trustState: string;
  createdAt: string;
  bindings: ComfyWorkflowBinding[];
}

export type ComfyBindingType =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "enum";

export interface ComfyWorkflowBinding {
  id: string;
  nodeId: string;
  inputName: string;
  valueType: ComfyBindingType;
  required: boolean;
  defaultValue?: unknown;
  minimum?: number;
  maximum?: number;
  choices: Array<string | number | boolean>;
  description: string;
}

export interface ComfyJobSummary {
  id: string;
  profileId: string;
  workflowRevisionId: string;
  status: string;
  createdAt: string;
  artifactStatus: "pending" | "ready" | "partial" | "failed" | "unavailable";
  artifactError?: string;
  artifacts: ComfyArtifactSummary[];
  error?: string;
}

export interface ComfyArtifactSummary {
  id: string;
  mediaType: string;
  originalFilename: string;
  sizeBytes: number;
  sha256: string;
  createdAt: string;
  verified: boolean;
  provenance: {
    workflowRevisionId: string;
    workflowDigest: string;
    remotePromptId: string;
    nodeId: string;
    category: string;
    outputIndex: number;
    fetchedAt: string;
    historySha256: string;
  };
}

export interface ComfyOverview {
  supportAvailable: boolean;
  profiles: ComfyProfileSummary[];
  workflows: ComfyWorkflowSummary[];
  jobs: ComfyJobSummary[];
}

export interface VoicePackSummary {
  id: string;
  name: string;
  installed: boolean;
  capabilities: string[];
  homepage?: string;
}

export interface VoiceEngineHealth {
  available: boolean;
  status: "ready" | "busy" | "offline" | "incompatible" | "error";
  detail: string;
}

export interface VoiceProfileSummary {
  id: string;
  name: string;
  mode: string;
  language: string;
  enabled: boolean;
}

export interface VoiceReferenceSummary {
  artifactId: string;
  mediaType: string;
  durationSeconds: number;
  sampleRateHz: number;
  channels: number;
  transcript?: string;
}

export interface VoiceProjectSummary {
  id: string;
  name: string;
  language: string;
  profileId: string;
  revision: number;
}

export interface VoiceJobSummary {
  id: string;
  projectId: string;
  enginePackId: string;
  status: string;
  createdAt: string;
  artifactIds: string[];
  error?: string;
}

export interface VoiceArtifactSummary {
  id: string;
  mediaType: string;
  format: string;
  sizeBytes: number;
  durationSeconds: number;
  createdAt: string;
}

export interface VoiceOverview {
  supportAvailable: boolean;
  packs: VoicePackSummary[];
  references: VoiceReferenceSummary[];
  profiles: VoiceProfileSummary[];
  projects: VoiceProjectSummary[];
  jobs: VoiceJobSummary[];
  artifacts: VoiceArtifactSummary[];
}

export interface VoiceReferenceFile {
  name: string;
  type: string;
  size: number;
  arrayBuffer: () => Promise<ArrayBuffer>;
}

export interface ProjectMasterConversation {
  id: string;
  startedAt: string;
  title: string | null;
  messageCount: number;
}

export interface ProjectMasterConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export type CommunicationFeedbackCategory =
  | "preserve_semantic_fidelity"
  | "avoid_unjustified_assumptions"
  | "avoid_unsolicited_advice"
  | "avoid_unnecessary_repetition"
  | "use_context_before_interpreting";

export interface ProjectMasterCommunicationPreference {
  key: string;
  value: string;
  source: string;
  confidence: number;
  scope: "global" | "situational" | string;
  supportingExamples: string[];
  status: string;
}

export interface ProjectMasterCommunicationProfile {
  preferences: ProjectMasterCommunicationPreference[];
  dislikedResponsePatterns: string[];
  correctionsCount: number;
}

interface ModelStatus {
  configured_model: string;
  num_ctx: number;
  ollama_reachable: boolean;
  models: string[];
  catalog?: unknown;
}

export interface StreamChatOptions {
  requestId: string;
  model: string;
  message: string;
  mode: ProjectMasterChatMode;
  allowMutations: boolean;
  projectId?: string;
  conversationId?: string;
  signal: AbortSignal;
  onToken: (token: string) => void;
  onConversation: (conversationId: string) => void;
  onActivity?: (activity: ProjectMasterRunActivity) => void;
  onRun?: (runId: string) => void;
}

interface StreamEvent {
  type?: unknown;
  content?: unknown;
  conversation_id?: unknown;
  run_id?: unknown;
  activity?: unknown;
  tool?: unknown;
  error?: unknown;
}

interface ConversationListResponse {
  conversations: Array<{
    id?: unknown;
    started_at?: unknown;
    title?: unknown;
    message_count?: unknown;
  }>;
}

interface ConversationResponse {
  id?: unknown;
  messages?: Array<{ role?: unknown; content?: unknown }>;
}

interface CommunicationProfileResponse {
  preferences?: Array<{
    key?: unknown;
    value?: unknown;
    source?: unknown;
    confidence?: unknown;
    scope?: unknown;
    supporting_examples?: unknown;
    status?: unknown;
  }>;
  disliked_response_patterns?: unknown;
  corrections?: unknown;
}

interface CommunicationFeedbackResponse {
  profile?: CommunicationProfileResponse;
}

export class ProjectMasterUnavailableError extends Error {
  constructor(message = API_UNREACHABLE_MESSAGE) {
    super(message);
    this.name = "ProjectMasterUnavailableError";
  }
}

interface ManagedBackendStatus {
  ready: boolean;
  started: boolean;
  sessionToken: string;
}

export async function ensureManagedBackend(): Promise<void> {
  if (!("__TAURI_INTERNALS__" in window)) return;

  try {
    const status = await invoke<ManagedBackendStatus>("ensure_backend");
    if (!status.ready) {
      throw new Error("The packaged Project Master backend did not report ready.");
    }
    if (!status.sessionToken) {
      throw new Error("The packaged Project Master backend did not return a session token.");
    }
    desktopSessionToken = status.sessionToken;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new ProjectMasterUnavailableError(
      `Project Master could not start its local backend. ${detail}`,
    );
  }
}

class ProjectMasterHttpError extends Error {
  constructor(readonly status: number, detail?: string) {
    super(detail || `Project Master returned HTTP ${status}.`);
    this.name = "ProjectMasterHttpError";
  }
}

class ProjectMasterProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProjectMasterProtocolError";
  }
}

function createAbortError(): DOMException {
  return new DOMException("The Project Master request was cancelled.", "AbortError");
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && /\b(abort|cancel)/i.test(error.message))
  );
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    const headers = new Headers(init?.headers);
    if (desktopSessionToken) {
      headers.set("X-Project-Master-Token", desktopSessionToken);
    }
    return await tauriFetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch (error) {
    if (init?.signal?.aborted || isAbortError(error)) throw createAbortError();
    throw new ProjectMasterUnavailableError();
  }
}

async function ensureSuccess(response: Response): Promise<void> {
  if (response.ok) return;
  let detail: string | undefined;
  try {
    const body = (await response.json()) as { detail?: unknown };
    detail = typeof body.detail === "string" ? body.detail : undefined;
  } catch {
    detail = undefined;
  }
  throw new ProjectMasterHttpError(response.status, detail);
}

export async function getModelStatus(signal?: AbortSignal): Promise<{
  models: ProjectMasterModel[];
  configuredModel: string;
  contextLength: number;
  ollamaReachable: boolean;
  teamCatalog: ProjectMasterTeamCatalogModel[];
  teamAvailable: boolean;
}> {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort, { once: true });
  const timeout = globalThis.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);
  try {
    const response = await request("/models/status", { signal: controller.signal });
    await ensureSuccess(response);
    const status = (await response.json()) as ModelStatus;
    if (!Array.isArray(status.models) || typeof status.configured_model !== "string") {
      throw new ProjectMasterProtocolError("Project Master returned an invalid model status.");
    }
    const teamCatalog = parseTeamCatalog(status.catalog);
    const modelCatalog = new Map(
      teamCatalog.flatMap((item) =>
        item.tags.map((tag) => [tag.toLocaleLowerCase(), item] as const),
      ),
    );
    const models = status.models.map((name) => {
      const catalogModel = modelCatalog.get(name.toLocaleLowerCase());
      const capabilities = catalogModel?.capabilities ?? [];
      return {
        name,
        capabilities,
        conversational: supportsConversationalCompletion(capabilities),
        toolCapable: capabilities.some((item) =>
          ["tool", "tools", "tool_calling"].includes(item.toLocaleLowerCase()),
        ),
      };
    });
    return {
      models,
      configuredModel: status.configured_model,
      contextLength: status.num_ctx,
      ollamaReachable: status.ollama_reachable,
      teamCatalog,
      teamAvailable: teamCatalog.some((item) =>
        supportsConversationalCompletion(item.capabilities),
      ),
    };
  } catch (error) {
    if (signal?.aborted) throw createAbortError();
    if (controller.signal.aborted) throw new ProjectMasterUnavailableError();
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

function supportsConversationalCompletion(capabilities: string[]): boolean {
  if (!capabilities.length) return true;
  return capabilities.some((item) =>
    ["chat", "completion", "generate"].includes(item.toLocaleLowerCase()),
  );
}

function parseTeamCatalog(payload: unknown): ProjectMasterTeamCatalogModel[] {
  if (payload === undefined) return [];
  if (!Array.isArray(payload)) {
    throw new ProjectMasterProtocolError("Project Master returned an invalid team catalog.");
  }
  return payload.map((model) => {
    if (
      typeof model !== "object" ||
      model === null ||
      !("physical_id" in model) ||
      typeof model.physical_id !== "string" ||
      !("primary_tag" in model) ||
      typeof model.primary_tag !== "string" ||
      !("tags" in model) ||
      !Array.isArray(model.tags) ||
      !model.tags.every((tag: unknown) => typeof tag === "string") ||
      !("capabilities" in model) ||
      !Array.isArray(model.capabilities) ||
      !model.capabilities.every(
        (capability: unknown) => typeof capability === "string",
      ) ||
      !("size_bytes" in model) ||
      typeof model.size_bytes !== "number"
    ) {
      throw new ProjectMasterProtocolError(
        "Project Master returned an invalid team catalog model.",
      );
    }
    return {
      physicalId: model.physical_id,
      primaryTag: model.primary_tag,
      tags: model.tags,
      capabilities: model.capabilities,
      sizeBytes: model.size_bytes,
    };
  });
}

export async function listConversations(
  signal?: AbortSignal,
): Promise<ProjectMasterConversation[]> {
  const response = await request("/conversations?limit=50", { signal });
  await ensureSuccess(response);
  const payload = (await response.json()) as ConversationListResponse;
  if (!Array.isArray(payload.conversations)) {
    throw new ProjectMasterProtocolError("Project Master returned invalid conversation data.");
  }
  return payload.conversations.map((conversation) => {
    if (
      typeof conversation.id !== "string" ||
      typeof conversation.started_at !== "string" ||
      (conversation.title !== null && typeof conversation.title !== "string") ||
      typeof conversation.message_count !== "number"
    ) {
      throw new ProjectMasterProtocolError("Project Master returned an invalid conversation.");
    }
    return {
      id: conversation.id,
      startedAt: conversation.started_at,
      title: conversation.title,
      messageCount: conversation.message_count,
    };
  });
}

export async function getConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<{ id: string; messages: ProjectMasterConversationMessage[] }> {
  const response = await request(`/conversations/${encodeURIComponent(conversationId)}`, {
    signal,
  });
  await ensureSuccess(response);
  const payload = (await response.json()) as ConversationResponse;
  if (typeof payload.id !== "string" || !Array.isArray(payload.messages)) {
    throw new ProjectMasterProtocolError("Project Master returned an invalid conversation.");
  }
  const messages = payload.messages.map((message) => {
    if (
      (message.role !== "user" && message.role !== "assistant") ||
      typeof message.content !== "string"
    ) {
      throw new ProjectMasterProtocolError("Project Master returned an invalid conversation message.");
    }
    return {
      role: message.role as ProjectMasterConversationMessage["role"],
      content: message.content,
    };
  });
  return { id: payload.id, messages };
}

export async function getCommunicationProfile(
  signal?: AbortSignal,
): Promise<ProjectMasterCommunicationProfile> {
  const response = await request("/profile/communication", { signal });
  await ensureSuccess(response);
  return parseCommunicationProfile((await response.json()) as CommunicationProfileResponse);
}

export async function submitCommunicationFeedback(
  category: CommunicationFeedbackCategory,
  note: string,
  scope: "global" | "situational" = "global",
): Promise<ProjectMasterCommunicationProfile> {
  const response = await request("/profile/communication/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category, note, scope }),
  });
  await ensureSuccess(response);
  const payload = (await response.json()) as CommunicationFeedbackResponse;
  if (!payload.profile) {
    throw new ProjectMasterProtocolError("Project Master returned invalid communication feedback.");
  }
  return parseCommunicationProfile(payload.profile);
}

function parseCommunicationProfile(
  payload: CommunicationProfileResponse,
): ProjectMasterCommunicationProfile {
  if (!Array.isArray(payload.preferences)) {
    throw new ProjectMasterProtocolError("Project Master returned an invalid communication profile.");
  }
  const preferences = payload.preferences.map((preference) => {
    if (
      typeof preference.key !== "string" ||
      typeof preference.value !== "string" ||
      typeof preference.source !== "string" ||
      typeof preference.confidence !== "number" ||
      typeof preference.scope !== "string" ||
      !Array.isArray(preference.supporting_examples) ||
      !preference.supporting_examples.every((example) => typeof example === "string") ||
      typeof preference.status !== "string"
    ) {
      throw new ProjectMasterProtocolError(
        "Project Master returned an invalid communication preference.",
      );
    }
    return {
      key: preference.key,
      value: preference.value,
      source: preference.source,
      confidence: preference.confidence,
      scope: preference.scope,
      supportingExamples: preference.supporting_examples,
      status: preference.status,
    };
  });
  if (
    !Array.isArray(payload.disliked_response_patterns) ||
    !payload.disliked_response_patterns.every((pattern) => typeof pattern === "string") ||
    !Array.isArray(payload.corrections)
  ) {
    throw new ProjectMasterProtocolError("Project Master returned an invalid communication profile.");
  }
  return {
    preferences,
    dislikedResponsePatterns: payload.disliked_response_patterns,
    correctionsCount: payload.corrections.length,
  };
}

type JsonRecord = Record<string, unknown>;

function record(value: unknown, label: string): JsonRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ProjectMasterProtocolError(`Project Master returned invalid ${label}.`);
  }
  return value as JsonRecord;
}

function stringField(
  value: JsonRecord,
  key: string,
  label: string,
  fallback = "",
): string {
  const field = value[key];
  if (field === undefined || field === null) return fallback;
  if (typeof field !== "string") {
    throw new ProjectMasterProtocolError(`Project Master returned invalid ${label}.`);
  }
  return field;
}

function arrayField(value: JsonRecord, key: string, label: string): unknown[] {
  const field = value[key];
  if (!Array.isArray(field)) {
    throw new ProjectMasterProtocolError(`Project Master returned invalid ${label}.`);
  }
  return field;
}

async function jsonRequest(path: string, init?: RequestInit): Promise<unknown> {
  const response = await request(path, init);
  await ensureSuccess(response);
  return response.json();
}

function jsonPost(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function parseProject(value: unknown): MasterProject {
  const item = record(value, "project");
  const metadata =
    typeof item.metadata === "object" &&
    item.metadata !== null &&
    !Array.isArray(item.metadata)
      ? (item.metadata as JsonRecord)
      : {};
  return {
    id: stringField(item, "id", "project"),
    name: stringField(item, "name", "project"),
    description: stringField(item, "description", "project"),
    status: stringField(item, "status", "project"),
    rootPath: stringField(item, "root_path", "project") || undefined,
    allowDreaming: metadata.allow_dreaming === true,
    updatedAt: stringField(item, "updated_at", "project"),
  };
}

function parseRun(value: unknown): MasterRun {
  const item = record(value, "run");
  return {
    id: stringField(item, "id", "run"),
    projectId: stringField(item, "project_id", "run"),
    kind: stringField(item, "kind", "run"),
    objective: stringField(item, "objective", "run"),
    mode: stringField(item, "mode", "run"),
    status: stringField(item, "status", "run"),
    createdAt: stringField(item, "created_at", "run"),
    completedAt: stringField(item, "completed_at", "run") || undefined,
  };
}

function parseRunEvent(value: unknown): MasterRunEvent {
  const item = record(value, "run event");
  if (typeof item.id !== "number") {
    throw new ProjectMasterProtocolError("Project Master returned invalid run event.");
  }
  return {
    id: item.id,
    type: stringField(item, "event_type", "run event"),
    summary: stringField(item, "summary", "run event"),
    createdAt: stringField(item, "created_at", "run event"),
  };
}

export async function listProjects(signal?: AbortSignal): Promise<MasterProject[]> {
  const payload = record(
    await jsonRequest("/projects", { signal }),
    "project list",
  );
  return arrayField(payload, "projects", "project list").map(parseProject);
}

export async function createProject(input: {
  name: string;
  description: string;
  rootPath?: string;
}): Promise<MasterProject> {
  return parseProject(
    await jsonRequest(
      "/projects",
      jsonPost({
        name: input.name,
        description: input.description,
        root_path: input.rootPath || null,
      }),
    ),
  );
}

export async function setProjectDreaming(
  projectId: string,
  enabled: boolean,
): Promise<MasterProject> {
  return parseProject(
    await jsonRequest(
      `/projects/${encodeURIComponent(projectId)}/dreaming`,
      jsonPost({ enabled }),
    ),
  );
}

export async function getProjectRuns(
  projectId: string,
  signal?: AbortSignal,
): Promise<MasterRun[]> {
  const payload = record(
    await jsonRequest(`/projects/${encodeURIComponent(projectId)}/runs`, { signal }),
    "run list",
  );
  return arrayField(payload, "runs", "run list").map(parseRun);
}

export async function getRunDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<{ run: MasterRun; events: MasterRunEvent[] }> {
  const payload = record(
    await jsonRequest(`/runs/${encodeURIComponent(runId)}`, { signal }),
    "run detail",
  );
  return {
    run: parseRun(payload.run),
    // Deliberately ignore arbitrary event payloads and worker drafts.
    events: arrayField(payload, "events", "run detail").map(parseRunEvent),
  };
}

function parseKnowledgeDocument(value: unknown): KnowledgeDocumentSummary {
  const item = record(value, "knowledge document");
  if (
    typeof item.version !== "number" ||
    typeof item.size_bytes !== "number" ||
    typeof item.active !== "boolean"
  ) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid knowledge document metadata.",
    );
  }
  return {
    id: stringField(item, "id", "knowledge document"),
    projectId: stringField(item, "project_id", "knowledge document"),
    // root_path is intentionally never copied into frontend state.
    relativePath: stringField(item, "relative_path", "knowledge document"),
    sha256: stringField(item, "content_sha256", "knowledge document"),
    version: item.version,
    mimeType: stringField(item, "mime_type", "knowledge document"),
    sizeBytes: item.size_bytes,
    indexedAt: stringField(item, "indexed_at", "knowledge document"),
    active: item.active,
  };
}

export async function listProjectKnowledge(
  projectId: string,
  includeHistory = true,
  signal?: AbortSignal,
): Promise<KnowledgeDocumentSummary[]> {
  const payload = record(
    await jsonRequest(
      `/projects/${encodeURIComponent(projectId)}/knowledge?include_history=${includeHistory}`,
      { signal },
    ),
    "knowledge document list",
  );
  return arrayField(payload, "documents", "knowledge document list").map(
    parseKnowledgeDocument,
  );
}

export async function indexProjectKnowledge(
  projectId: string,
  relativePath = ".",
): Promise<KnowledgeIndexSummary> {
  const payload = record(
    await jsonRequest(
      `/projects/${encodeURIComponent(projectId)}/knowledge/index`,
      jsonPost({ relative_path: relativePath, prune: true }),
    ),
    "knowledge index result",
  );
  const numeric = ["indexed", "unchanged", "skipped", "archived"] as const;
  if (numeric.some((key) => typeof payload[key] !== "number")) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid knowledge index counts.",
    );
  }
  return {
    projectId: stringField(payload, "project_id", "knowledge index result"),
    indexed: payload.indexed as number,
    unchanged: payload.unchanged as number,
    skipped: payload.skipped as number,
    archived: payload.archived as number,
    errorCount: Array.isArray(payload.errors) ? payload.errors.length : 0,
  };
}

function parseKnowledgeHit(value: unknown): KnowledgeSearchHit {
  const item = record(value, "knowledge search result");
  if (
    typeof item.score !== "number" ||
    typeof item.document_version !== "number"
  ) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid knowledge search metadata.",
    );
  }
  return {
    documentId: stringField(item, "document_id", "knowledge search result"),
    relativePath: stringField(item, "relative_path", "knowledge search result"),
    citation: stringField(item, "citation", "knowledge search result"),
    excerpt: stringField(item, "content", "knowledge search result"),
    score: item.score,
    sha256: stringField(item, "content_sha256", "knowledge search result"),
    documentVersion: item.document_version,
  };
}

export async function searchProjectKnowledge(
  projectId: string,
  query: string,
  signal?: AbortSignal,
): Promise<KnowledgeSearchHit[]> {
  const parameters = new URLSearchParams({ query, limit: "8" });
  const payload = record(
    await jsonRequest(
      `/projects/${encodeURIComponent(projectId)}/knowledge/search?${parameters}`,
      { signal },
    ),
    "knowledge search response",
  );
  return arrayField(payload, "results", "knowledge search response").map(
    parseKnowledgeHit,
  );
}

function parseApproval(value: unknown): MasterApproval {
  const item = record(value, "approval");
  return {
    id: stringField(item, "id", "approval"),
    runId: stringField(item, "run_id", "approval"),
    actionKind: stringField(item, "action_kind", "approval"),
    target: stringField(item, "target", "approval"),
    risk: stringField(item, "risk", "approval"),
    reversible: Boolean(item.reversible),
    rollbackPlan: stringField(item, "rollback_plan", "approval"),
    status: stringField(item, "status", "approval"),
    createdAt: stringField(item, "created_at", "approval"),
  };
}

export async function listApprovals(
  status: "pending" | "all" = "pending",
  signal?: AbortSignal,
): Promise<MasterApproval[]> {
  const payload = record(
    await jsonRequest(`/approvals?status=${status}`, { signal }),
    "approval list",
  );
  return arrayField(payload, "approvals", "approval list").map(parseApproval);
}

export async function resolveApproval(
  approvalId: string,
  status: "approved" | "rejected",
  note: string,
): Promise<void> {
  await jsonRequest(
    `/approvals/${encodeURIComponent(approvalId)}/resolve`,
    jsonPost({ status, note }),
  );
}

export async function getToolStatus(signal?: AbortSignal): Promise<{
  workspaceRoot: string;
  writesEnabled: boolean;
  tools: Array<{ name: string; description: string; enabled: boolean }>;
}> {
  const payload = record(await jsonRequest("/tools/status", { signal }), "tool status");
  return {
    workspaceRoot: stringField(payload, "workspace_root", "tool status"),
    writesEnabled: Boolean(payload.workspace_writes_enabled),
    tools: arrayField(payload, "tools", "tool status").map((raw) => {
      const tool = record(raw, "tool");
      return {
        name: stringField(tool, "name", "tool"),
        description: stringField(tool, "description", "tool"),
        enabled: Boolean(tool.enabled),
      };
    }),
  };
}

function parseDreamRecipe(value: unknown): DreamRecipeSummary {
  const item = record(value, "Dream recipe");
  const sourceScopes = arrayField(
    item,
    "source_scopes",
    "Dream recipe",
  );
  if (!sourceScopes.every((scope): scope is string => typeof scope === "string")) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid Dream recipe source scopes.",
    );
  }
  return {
    recipeId: stringField(item, "recipe_id", "Dream recipe"),
    name: stringField(item, "name", "Dream recipe"),
    objective: stringField(item, "objective", "Dream recipe"),
    kind: stringField(item, "kind", "Dream recipe"),
    sourceScopes,
    version: typeof item.version === "number" ? item.version : 1,
  };
}

function parseDreamRun(value: unknown): DreamRunSummary {
  const item = record(value, "Dream run");
  return {
    runId: stringField(item, "run_id", "Dream run"),
    recipeId: stringField(item, "recipe_id", "Dream run"),
    status: stringField(item, "status", "Dream run"),
    createdAt: stringField(item, "created_at_utc", "Dream run"),
    itemId: stringField(item, "item_id", "Dream run") || undefined,
    error: stringField(item, "error", "Dream run") || undefined,
  };
}

function parseDreamItem(value: unknown): DreamInboxItem {
  const item = record(value, "Dream Inbox item");
  const refs = item.source_refs;
  if (!Array.isArray(refs) || !refs.every((ref: unknown) => typeof ref === "string")) {
    throw new ProjectMasterProtocolError("Project Master returned invalid Dream Inbox item.");
  }
  return {
    itemId: stringField(item, "item_id", "Dream Inbox item"),
    recipeId: stringField(item, "recipe_id", "Dream Inbox item"),
    proposalText: stringField(item, "proposal_text", "Dream Inbox item"),
    epistemicLabel: stringField(item, "epistemic_label", "Dream Inbox item"),
    sourceRefs: refs,
    disposition: stringField(item, "disposition", "Dream Inbox item"),
    createdAt: stringField(item, "created_at_utc", "Dream Inbox item"),
  };
}

function numberField(
  value: JsonRecord,
  key: string,
  label: string,
  fallback = 0,
): number {
  const field = value[key];
  if (field === undefined || field === null) return fallback;
  if (typeof field !== "number" || !Number.isFinite(field)) {
    throw new ProjectMasterProtocolError(`Project Master returned invalid ${label}.`);
  }
  return field;
}

function parseDreamSchedule(value: unknown): DreamScheduleSummary {
  const item = record(value, "Dream schedule");
  const rules = record(item.resource_rules, "Dream resource rules");
  const quiet = item.quiet_window
    ? record(item.quiet_window, "Dream quiet window")
    : undefined;
  const catchUp = stringField(item, "catch_up", "Dream schedule");
  if (!["skip", "latest", "all_bounded"].includes(catchUp)) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid Dream schedule.",
    );
  }
  const weekdays = quiet
    ? arrayField(quiet, "weekdays", "Dream quiet window").filter(
        (day): day is number => typeof day === "number",
      )
    : [];
  return {
    scheduleId: stringField(item, "schedule_id", "Dream schedule"),
    recipeId: stringField(item, "recipe_id", "Dream schedule"),
    timezone: stringField(item, "timezone", "Dream schedule"),
    localTime: stringField(item, "local_time", "Dream schedule"),
    enabled: item.enabled === true,
    catchUp: catchUp as DreamScheduleSummary["catchUp"],
    onTimeGraceSeconds: numberField(
      item,
      "on_time_grace_seconds",
      "Dream schedule",
      900,
    ),
    maxLookbackDays: numberField(item, "max_lookback_days", "Dream schedule", 7),
    maxCatchUpWindows: numberField(
      item,
      "max_catch_up_windows",
      "Dream schedule",
      3,
    ),
    resourceRules: {
      minIdleSeconds: numberField(
        rules,
        "min_idle_seconds",
        "Dream resource rules",
        300,
      ),
      maxCpuPercent: numberField(
        rules,
        "max_cpu_percent",
        "Dream resource rules",
        60,
      ),
      minAvailableMemoryBytes: numberField(
        rules,
        "min_available_memory_bytes",
        "Dream resource rules",
        2 * 1024 ** 3,
      ),
      minGpuFreeBytes:
        rules.min_gpu_free_bytes === null ||
        rules.min_gpu_free_bytes === undefined
          ? undefined
          : numberField(rules, "min_gpu_free_bytes", "Dream resource rules"),
      requireNoModelJobs: rules.require_no_model_jobs !== false,
      requireAcPower: rules.require_ac_power === true,
    },
    quietWindow: quiet
      ? {
          timezone: stringField(quiet, "timezone", "Dream quiet window"),
          startLocal: stringField(quiet, "start_local", "Dream quiet window"),
          endLocal: stringField(quiet, "end_local", "Dream quiet window"),
          weekdays,
        }
      : undefined,
    version: numberField(item, "version", "Dream schedule", 1),
    updatedAt: stringField(item, "updated_at_utc", "Dream schedule"),
  };
}

export async function getDreamOverview(signal?: AbortSignal): Promise<DreamOverview> {
  const payload = record(await jsonRequest("/dreams", { signal }), "Dream overview");
  return {
    proposalOnly: payload.proposal_only === true,
    scheduledExecutionEnabled: payload.scheduled_execution_enabled === true,
    backgroundConfigured: payload.background_configured === true,
    recipes: arrayField(payload, "recipes", "Dream overview").map(parseDreamRecipe),
    schedules: arrayField(payload, "schedules", "Dream overview").map(
      parseDreamSchedule,
    ),
    runs: arrayField(payload, "runs", "Dream overview").map(parseDreamRun),
    inbox: arrayField(payload, "inbox", "Dream overview").map(parseDreamItem),
  };
}

export async function saveDreamRecipe(input: {
  recipeId: string;
  name: string;
  objective: string;
  sourceScopes: string[];
}): Promise<DreamRecipeSummary> {
  return parseDreamRecipe(
    await jsonRequest(
      "/dreams/recipes",
      jsonPost({
        recipe_id: input.recipeId,
        name: input.name,
        objective: input.objective,
        source_scopes: input.sourceScopes,
      }),
    ),
  );
}

export async function saveDreamSchedule(input: {
  scheduleId: string;
  recipeId: string;
  timezone: string;
  localTime: string;
  enabled: boolean;
  catchUp: DreamScheduleSummary["catchUp"];
  onTimeGraceSeconds: number;
  maxLookbackDays: number;
  maxCatchUpWindows: number;
  resourceRules: DreamResourceRules;
  quietWindow?: DreamQuietWindow;
  expectedVersion?: number;
}): Promise<DreamScheduleSummary> {
  return parseDreamSchedule(
    await jsonRequest(
      "/dreams/schedules",
      jsonPost({
        schedule_id: input.scheduleId,
        recipe_id: input.recipeId,
        timezone: input.timezone,
        local_time: input.localTime,
        enabled: input.enabled,
        catch_up: input.catchUp,
        on_time_grace_seconds: input.onTimeGraceSeconds,
        max_lookback_days: input.maxLookbackDays,
        max_catch_up_windows: input.maxCatchUpWindows,
        resource_rules: {
          min_idle_seconds: input.resourceRules.minIdleSeconds,
          max_cpu_percent: input.resourceRules.maxCpuPercent,
          min_available_memory_bytes:
            input.resourceRules.minAvailableMemoryBytes,
          min_gpu_free_bytes: input.resourceRules.minGpuFreeBytes ?? null,
          require_no_model_jobs: input.resourceRules.requireNoModelJobs,
          require_ac_power: input.resourceRules.requireAcPower,
        },
        quiet_window: input.quietWindow
          ? {
              timezone: input.quietWindow.timezone,
              start_local: input.quietWindow.startLocal,
              end_local: input.quietWindow.endLocal,
              weekdays: input.quietWindow.weekdays,
            }
          : null,
        expected_version: input.expectedVersion ?? null,
      }),
    ),
  );
}

export async function setDreamScheduleEnabled(
  scheduleId: string,
  enabled: boolean,
): Promise<DreamScheduleSummary> {
  return parseDreamSchedule(
    await jsonRequest(
      `/dreams/schedules/${encodeURIComponent(scheduleId)}/enabled`,
      jsonPost({ enabled }),
    ),
  );
}

export async function deleteDreamSchedule(scheduleId: string): Promise<void> {
  const response = await request(
    `/dreams/schedules/${encodeURIComponent(scheduleId)}`,
    { method: "DELETE" },
  );
  await ensureSuccess(response);
}

export async function runManualDream(input: {
  recipeId: string;
  sourceId: string;
  locator: string;
  content: string;
}): Promise<void> {
  await jsonRequest(
    "/dreams/runs/manual",
    jsonPost({
      recipe_id: input.recipeId,
      sources: [
        {
          source_id: input.sourceId,
          kind: "user_note",
          locator: input.locator,
          content: input.content,
          sensitivity: "internal",
          allow_dreaming: true,
        },
      ],
    }),
  );
}

export async function decideDreamItem(
  itemId: string,
  decision: "promote" | "reject",
  rationale: string,
  target = "project_idea_candidate",
): Promise<void> {
  const body = decision === "promote" ? { rationale, target } : { rationale };
  await jsonRequest(
    `/dreams/inbox/${encodeURIComponent(itemId)}/${decision}`,
    jsonPost(body),
  );
}

function parseComfyProfile(value: unknown): ComfyProfileSummary {
  const item = record(value, "ComfyUI profile");
  return {
    id: stringField(item, "id", "ComfyUI profile"),
    name: stringField(item, "name", "ComfyUI profile"),
    baseUrl: stringField(item, "base_url", "ComfyUI profile"),
    verifyTls: item.verify_tls !== false,
  };
}

function parseComfyWorkflow(value: unknown): ComfyWorkflowSummary {
  const stored = record(value, "ComfyUI workflow");
  const revision = record(stored.revision, "ComfyUI workflow revision");
  const bindings = arrayField(revision, "bindings", "ComfyUI workflow").map(
    (raw): ComfyWorkflowBinding => {
      const binding = record(raw, "ComfyUI workflow binding");
      const valueType = stringField(
        binding,
        "value_type",
        "ComfyUI workflow binding",
      );
      if (
        !["string", "integer", "number", "boolean", "enum"].includes(valueType)
      ) {
        throw new ProjectMasterProtocolError(
          "Project Master returned invalid ComfyUI workflow binding.",
        );
      }
      const choices = arrayField(
        binding,
        "choices",
        "ComfyUI workflow binding",
      ).filter(
        (choice): choice is string | number | boolean =>
          ["string", "number", "boolean"].includes(typeof choice),
      );
      return {
        id: stringField(binding, "id", "ComfyUI workflow binding"),
        nodeId: stringField(binding, "node_id", "ComfyUI workflow binding"),
        inputName: stringField(
          binding,
          "input_name",
          "ComfyUI workflow binding",
        ),
        valueType: valueType as ComfyBindingType,
        required: binding.required !== false,
        defaultValue:
          binding.default_value === null ? undefined : binding.default_value,
        minimum:
          binding.minimum === null || binding.minimum === undefined
            ? undefined
            : numberField(binding, "minimum", "ComfyUI workflow binding"),
        maximum:
          binding.maximum === null || binding.maximum === undefined
            ? undefined
            : numberField(binding, "maximum", "ComfyUI workflow binding"),
        choices,
        description: stringField(
          binding,
          "description",
          "ComfyUI workflow binding",
        ),
      };
    },
  );
  return {
    id: stringField(revision, "id", "ComfyUI workflow"),
    name: stringField(revision, "name", "ComfyUI workflow"),
    digest: stringField(revision, "digest", "ComfyUI workflow"),
    trustState: stringField(stored, "trust_state", "ComfyUI workflow"),
    createdAt: stringField(revision, "created_at", "ComfyUI workflow"),
    bindings,
  };
}

function parseComfyJob(value: unknown): ComfyJobSummary {
  const item = record(value, "ComfyUI job");
  const artifactStatus = stringField(
    item,
    "artifact_status",
    "ComfyUI job",
  );
  if (
    !["pending", "ready", "partial", "failed", "unavailable"].includes(
      artifactStatus,
    )
  ) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid ComfyUI artifact status.",
    );
  }
  return {
    id: stringField(item, "id", "ComfyUI job"),
    profileId: stringField(item, "profile_id", "ComfyUI job"),
    workflowRevisionId: stringField(item, "workflow_revision_id", "ComfyUI job"),
    status: stringField(item, "status", "ComfyUI job"),
    createdAt: stringField(item, "created_at", "ComfyUI job"),
    artifactStatus: artifactStatus as ComfyJobSummary["artifactStatus"],
    artifactError:
      stringField(item, "artifact_error", "ComfyUI job") || undefined,
    artifacts: arrayField(item, "artifacts", "ComfyUI job").map(
      parseComfyArtifact,
    ),
    error: stringField(item, "error", "ComfyUI job") || undefined,
  };
}

function parseComfyArtifact(value: unknown): ComfyArtifactSummary {
  const item = record(value, "ComfyUI artifact");
  const provenance = record(item.provenance, "ComfyUI artifact provenance");
  const output = record(provenance.output, "ComfyUI artifact output");
  return {
    id: stringField(item, "id", "ComfyUI artifact"),
    mediaType: stringField(item, "media_type", "ComfyUI artifact"),
    originalFilename: stringField(
      item,
      "original_filename",
      "ComfyUI artifact",
    ),
    sizeBytes: numberField(item, "size_bytes", "ComfyUI artifact"),
    sha256: stringField(item, "sha256", "ComfyUI artifact"),
    createdAt: stringField(item, "created_at", "ComfyUI artifact"),
    verified: item.verified === true,
    provenance: {
      workflowRevisionId: stringField(
        provenance,
        "workflow_revision_id",
        "ComfyUI artifact provenance",
      ),
      workflowDigest: stringField(
        provenance,
        "workflow_digest",
        "ComfyUI artifact provenance",
      ),
      remotePromptId: stringField(
        provenance,
        "remote_prompt_id",
        "ComfyUI artifact provenance",
      ),
      nodeId: stringField(output, "node_id", "ComfyUI artifact output"),
      category: stringField(output, "category", "ComfyUI artifact output"),
      outputIndex: numberField(
        output,
        "output_index",
        "ComfyUI artifact output",
      ),
      fetchedAt: stringField(
        provenance,
        "fetched_at",
        "ComfyUI artifact provenance",
      ),
      historySha256: stringField(
        provenance,
        "history_sha256",
        "ComfyUI artifact provenance",
      ),
    },
  };
}

export async function getComfyOverview(signal?: AbortSignal): Promise<ComfyOverview> {
  const payload = record(
    await jsonRequest("/integrations/comfyui", { signal }),
    "ComfyUI overview",
  );
  return {
    supportAvailable: payload.support_available === true,
    profiles: arrayField(payload, "profiles", "ComfyUI overview").map(parseComfyProfile),
    workflows: arrayField(payload, "workflows", "ComfyUI overview").map(parseComfyWorkflow),
    jobs: arrayField(payload, "jobs", "ComfyUI overview").map(parseComfyJob),
  };
}

export async function saveComfyProfile(input: {
  id: string;
  name: string;
  baseUrl: string;
  trustedHosts?: string[];
}): Promise<ComfyProfileSummary> {
  return parseComfyProfile(
    await jsonRequest(
      "/integrations/comfyui/profiles",
      jsonPost({
        id: input.id,
        name: input.name,
        base_url: input.baseUrl,
        trusted_hosts: input.trustedHosts ?? [],
        verify_tls: true,
      }),
    ),
  );
}

export async function getComfyProfileStatus(profileId: string): Promise<{
  ok: boolean;
  error?: string;
}> {
  const payload = record(
    await jsonRequest(
      `/integrations/comfyui/profiles/${encodeURIComponent(profileId)}/status`,
    ),
    "ComfyUI status",
  );
  return {
    ok: payload.ok === true,
    error: stringField(payload, "error", "ComfyUI status") || undefined,
  };
}

export async function importComfyWorkflow(
  name: string,
  workflow: JsonRecord,
  bindings: ComfyWorkflowBinding[] = [],
): Promise<ComfyWorkflowSummary> {
  return parseComfyWorkflow(
    await jsonRequest(
      "/integrations/comfyui/workflows",
      jsonPost({
        name,
        workflow,
        bindings: bindings.map((binding) => ({
          id: binding.id,
          node_id: binding.nodeId,
          input_name: binding.inputName,
          value_type: binding.valueType,
          required: binding.required,
          default_value: binding.defaultValue ?? null,
          minimum: binding.minimum ?? null,
          maximum: binding.maximum ?? null,
          choices: binding.choices,
          description: binding.description,
        })),
      }),
    ),
  );
}

export async function decideComfyWorkflow(
  revisionId: string,
  trustState: "approved" | "rejected",
  note: string,
): Promise<void> {
  await jsonRequest(
    `/integrations/comfyui/workflows/${encodeURIComponent(revisionId)}/decision`,
    jsonPost({ trust_state: trustState, note }),
  );
}

export async function createComfyJob(input: {
  profileId: string;
  workflowRevisionId: string;
  values?: Record<string, unknown>;
}): Promise<void> {
  await jsonRequest(
    "/integrations/comfyui/jobs",
    jsonPost({
      profile_id: input.profileId,
      workflow_revision_id: input.workflowRevisionId,
      values: input.values ?? {},
    }),
  );
}

export async function cancelComfyJob(jobId: string): Promise<void> {
  await jsonRequest(
    `/integrations/comfyui/jobs/${encodeURIComponent(jobId)}/cancel`,
    jsonPost({}),
  );
}

export async function getComfyArtifactContent(
  jobId: string,
  artifactId: string,
): Promise<Blob> {
  const response = await request(
    `/integrations/comfyui/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}/content`,
  );
  await ensureSuccess(response);
  return response.blob();
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function parseVoiceProfile(value: unknown): VoiceProfileSummary {
  const item = record(value, "voice profile");
  return {
    id: stringField(item, "id", "voice profile"),
    name: stringField(item, "name", "voice profile"),
    mode: stringField(item, "mode", "voice profile"),
    language: stringField(item, "language", "voice profile"),
    enabled: item.enabled !== false,
  };
}

function parseVoiceReference(value: unknown): VoiceReferenceSummary {
  const item = record(value, "voice reference");
  if (
    typeof item.duration_seconds !== "number" ||
    typeof item.sample_rate_hz !== "number" ||
    typeof item.channels !== "number"
  ) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid voice reference metadata.",
    );
  }
  return {
    artifactId: stringField(item, "artifact_id", "voice reference"),
    mediaType: stringField(item, "media_type", "voice reference"),
    durationSeconds: item.duration_seconds,
    sampleRateHz: item.sample_rate_hz,
    channels: item.channels,
    transcript: stringField(item, "transcript", "voice reference") || undefined,
  };
}

function parseVoiceProject(value: unknown): VoiceProjectSummary {
  const item = record(value, "voice project");
  return {
    id: stringField(item, "id", "voice project"),
    name: stringField(item, "name", "voice project"),
    language: stringField(item, "language", "voice project"),
    profileId: stringField(item, "default_voice_profile_id", "voice project"),
    revision: typeof item.revision === "number" ? item.revision : 1,
  };
}

function parseVoiceJob(value: unknown): VoiceJobSummary {
  const item = record(value, "voice job");
  const chunks = Array.isArray(item.chunks) ? item.chunks : [];
  const artifactIds = chunks.flatMap((raw) => {
    const chunk = typeof raw === "object" && raw !== null ? raw as JsonRecord : {};
    return typeof chunk.artifact_id === "string" ? [chunk.artifact_id] : [];
  });
  return {
    id: stringField(item, "id", "voice job"),
    projectId: stringField(item, "project_id", "voice job"),
    enginePackId: stringField(item, "engine_pack_id", "voice job"),
    status: stringField(item, "status", "voice job"),
    createdAt: stringField(item, "created_at", "voice job"),
    artifactIds,
    error: stringField(item, "error", "voice job") || undefined,
  };
}

function parseVoiceArtifact(value: unknown): VoiceArtifactSummary {
  const item = record(value, "voice artifact");
  return {
    id: stringField(item, "id", "voice artifact"),
    mediaType: stringField(item, "media_type", "voice artifact"),
    format: stringField(item, "format", "voice artifact"),
    sizeBytes: typeof item.size_bytes === "number" ? item.size_bytes : 0,
    durationSeconds:
      typeof item.duration_seconds === "number" ? item.duration_seconds : 0,
    createdAt: stringField(item, "created_at", "voice artifact"),
  };
}

export async function getVoiceOverview(signal?: AbortSignal): Promise<VoiceOverview> {
  const payload = record(await jsonRequest("/voice", { signal }), "voice overview");
  const installed = arrayField(payload, "installed_packs", "voice overview").map((raw) => {
    const item = record(raw, "voice engine pack");
    return {
      id: stringField(item, "id", "voice engine pack"),
      name: stringField(item, "engine_id", "voice engine pack"),
      installed: true,
      capabilities: stringArray(item.capabilities),
    };
  });
  const optional = arrayField(payload, "optional_pack_templates", "voice overview").map(
    (raw) => {
      const item = record(raw, "voice engine template");
      return {
        id: stringField(item, "id", "voice engine template"),
        name: stringField(item, "display_name", "voice engine template"),
        installed: item.installed === true,
        capabilities: stringArray(item.capabilities),
        homepage: stringField(item, "upstream_homepage", "voice engine template") || undefined,
      };
    },
  );
  const packs = [
    ...installed,
    ...optional.filter((item) => !item.installed),
  ];
  return {
    supportAvailable: payload.support_available === true,
    packs,
    references: arrayField(payload, "references", "voice overview").map(
      parseVoiceReference,
    ),
    profiles: arrayField(payload, "profiles", "voice overview").map(parseVoiceProfile),
    projects: arrayField(payload, "projects", "voice overview").map(parseVoiceProject),
    jobs: arrayField(payload, "jobs", "voice overview").map(parseVoiceJob),
    artifacts: arrayField(payload, "artifacts", "voice overview").map(parseVoiceArtifact),
  };
}

export async function getVoiceEngineHealth(
  packId: string,
  signal?: AbortSignal,
): Promise<VoiceEngineHealth> {
  const payload = record(
    await jsonRequest(`/voice/engines/${encodeURIComponent(packId)}/health`, {
      signal,
    }),
    "voice engine health",
  );
  const status = stringField(payload, "status", "voice engine health");
  if (!["ready", "busy", "offline", "incompatible", "error"].includes(status)) {
    throw new ProjectMasterProtocolError(
      "Project Master returned invalid voice engine health.",
    );
  }
  return {
    available: payload.available === true,
    status: status as VoiceEngineHealth["status"],
    detail: stringField(payload, "detail", "voice engine health"),
  };
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 32_768;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return globalThis.btoa(binary);
}

export async function importVoiceReference(
  file: VoiceReferenceFile,
  transcript?: string,
): Promise<VoiceReferenceSummary> {
  if (
    !file.name.toLowerCase().endsWith(".wav") ||
    (file.type && !["audio/wav", "audio/x-wav", "audio/wave"].includes(file.type))
  ) {
    throw new Error("Voice references must be local WAV files.");
  }
  if (file.size < 44 || file.size > 67_000_000) {
    throw new Error("Voice reference WAV must be between 44 bytes and 67 MB.");
  }
  const payload = await jsonRequest(
    "/voice/references",
    jsonPost({
      file_name: file.name,
      audio_base64: bufferToBase64(await file.arrayBuffer()),
      transcript: transcript?.trim() || null,
    }),
  );
  return parseVoiceReference(payload);
}

export async function saveReferenceVoiceProfile(input: {
  profileId: string;
  name: string;
  language: string;
  description: string;
  referenceArtifactId: string;
  rightsBasis:
    | "self_voice"
    | "explicit_consent"
    | "licensed_voice"
    | "synthetic_reference";
  subjectLabel: string;
  publication: boolean;
  commercial: boolean;
  notes: string;
  evidenceArtifactIds?: string[];
}): Promise<void> {
  const scopes = ["voice_generation"];
  if (input.publication) scopes.push("publication");
  if (input.commercial) scopes.push("commercial_use");
  await jsonRequest(
    "/voice/profiles/reference",
    jsonPost({
      profile_id: input.profileId,
      name: input.name,
      language: input.language,
      description: input.description,
      reference_artifact_ids: [input.referenceArtifactId],
      rights_basis: input.rightsBasis,
      scopes,
      subject_label: input.subjectLabel,
      attested_by_user: true,
      // The reference WAV is a voice sample, not proof of consent or a license.
      // A future evidence-document endpoint can populate this independently.
      evidence_artifact_ids: input.evidenceArtifactIds ?? [],
      notes: input.notes,
    }),
  );
}

export async function saveDesignedVoiceProfile(input: {
  profileId: string;
  name: string;
  language: string;
  description: string;
  publication: boolean;
}): Promise<void> {
  await jsonRequest(
    "/voice/profiles/designed",
    jsonPost({
      profile_id: input.profileId,
      name: input.name,
      language: input.language,
      description: input.description,
      scopes: input.publication
        ? ["voice_generation", "publication"]
        : ["voice_generation"],
      attested_by_user: true,
    }),
  );
}

export async function saveVoiceProject(input: {
  projectId: string;
  name: string;
  language: string;
  profileId: string;
  script: string;
}): Promise<void> {
  await jsonRequest(
    "/voice/projects",
    jsonPost({
      project_id: input.projectId,
      name: input.name,
      language: input.language,
      default_voice_profile_id: input.profileId,
      blocks: [{ id: "block-1", kind: "narration", text: input.script }],
    }),
  );
}

export async function createVoiceJob(input: {
  projectId: string;
  packId: string;
  purpose: "private" | "publication" | "commercial";
}): Promise<void> {
  await jsonRequest(
    "/voice/jobs",
    jsonPost({
      project_id: input.projectId,
      engine_pack_id: input.packId,
      purpose: input.purpose,
    }),
  );
}

export async function runVoiceJob(jobId: string): Promise<void> {
  await jsonRequest(`/voice/jobs/${encodeURIComponent(jobId)}/run`, jsonPost({}));
}

export async function cancelVoiceJob(jobId: string): Promise<void> {
  await jsonRequest(`/voice/jobs/${encodeURIComponent(jobId)}/cancel`, jsonPost({}));
}

export async function getVoiceArtifactContent(artifactId: string): Promise<Blob> {
  const response = await request(
    `/voice/artifacts/${encodeURIComponent(artifactId)}/content`,
  );
  await ensureSuccess(response);
  return response.blob();
}

function parseEvent(line: string, options: StreamChatOptions): boolean {
  if (!line.trim()) return false;
  let event: StreamEvent;
  try {
    event = JSON.parse(line) as StreamEvent;
  } catch {
    throw new ProjectMasterProtocolError("Project Master returned invalid stream data.");
  }
  if (event.type === "start" && typeof event.conversation_id === "string") {
    options.onConversation(event.conversation_id);
  }
  if (typeof event.run_id === "string") {
    options.onRun?.(event.run_id);
  }
  if (event.type === "token" && typeof event.content === "string") {
    options.onToken(event.content);
  }
  if (event.type === "team") {
    const activity = parseTeamActivity(event.activity, event.run_id);
    if (activity) options.onActivity?.(activity);
  }
  if (event.type === "tool") {
    const activity = parseToolActivity(event.tool, event.run_id);
    if (activity) options.onActivity?.(activity);
  }
  if (event.type === "error") {
    throw new Error(typeof event.error === "string" ? event.error : "Backend stream failed.");
  }
  if (event.type === "cancelled") throw createAbortError();
  return event.type === "done";
}

function parseTeamActivity(
  payload: unknown,
  runId: unknown,
): ProjectMasterRunActivity | null {
  if (typeof payload !== "object" || payload === null) return null;
  const kind = "type" in payload && typeof payload.type === "string"
    ? payload.type
    : "team_activity";
  const message = "message" in payload && typeof payload.message === "string"
    ? payload.message
    : "Team activity updated";
  const member = "member" in payload && typeof payload.member === "object"
    ? payload.member
    : null;
  const model = member && "model" in member && typeof member.model === "string"
    ? member.model
    : undefined;
  const role = member && "role" in member && typeof member.role === "string"
    ? member.role
    : undefined;
  return {
    kind,
    message,
    runId: typeof runId === "string" ? runId : undefined,
    model,
    role,
  };
}

function parseToolActivity(
  payload: unknown,
  runId: unknown,
): ProjectMasterRunActivity | null {
  if (typeof payload !== "object" || payload === null) return null;
  const tool = "name" in payload && typeof payload.name === "string"
    ? payload.name
    : "tool";
  const ok = "ok" in payload && typeof payload.ok === "boolean"
    ? payload.ok
    : undefined;
  return {
    kind: ok === false ? "tool_failed" : "tool_completed",
    message: `${tool} ${ok === false ? "failed" : "completed"}`,
    runId: typeof runId === "string" ? runId : undefined,
    tool,
    ok,
    inputDetail:
      "arguments" in payload
        ? formatToolDetail(payload.arguments, "No structured input")
        : undefined,
    outputDetail:
      "result" in payload
        ? formatToolDetail(payload.result, "No output")
        : undefined,
  };
}

const TOOL_DETAIL_LIMIT = 4_000;
const SECRET_KEY =
  /^(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)$/i;

function redactToolValue(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[depth limit]";
  if (Array.isArray(value)) {
    return value
      .slice(0, 50)
      .map((item) => redactToolValue(item, depth + 1));
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value as JsonRecord)
        .slice(0, 80)
        .map(([key, item]) => [
          key,
          SECRET_KEY.test(key) ? "[redacted]" : redactToolValue(item, depth + 1),
        ]),
    );
  }
  if (typeof value === "string") {
    return value
      .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
      .replace(
        /\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi,
        "$1=[redacted]",
      );
  }
  return value;
}

function formatToolDetail(value: unknown, emptyLabel: string): string {
  let normalized = value;
  if (typeof value === "string") {
    try {
      normalized = JSON.parse(value) as unknown;
    } catch {
      normalized = value;
    }
  }
  const redacted = redactToolValue(normalized);
  const rendered =
    typeof redacted === "string"
      ? redacted
      : JSON.stringify(redacted, null, 2) ?? emptyLabel;
  if (!rendered) return emptyLabel;
  if (rendered.length <= TOOL_DETAIL_LIMIT) return rendered;
  return `${rendered.slice(0, TOOL_DETAIL_LIMIT - 24)}\n… [detail truncated]`;
}

export async function cancelChat(requestId: string): Promise<void> {
  const response = await request("/chat/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  });
  await ensureSuccess(response);
}

export async function streamChat(options: StreamChatOptions): Promise<void> {
  const response = await request("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: options.requestId,
      message: options.message,
      conversation_id: options.conversationId,
      model: options.model,
      mode: options.mode,
      allow_mutations: options.allowMutations,
      project_id: options.projectId,
    }),
    signal: options.signal,
  });
  await ensureSuccess(response);
  if (!response.body) {
    throw new ProjectMasterProtocolError("Project Master returned no response stream.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneEvent = false;
  try {
    while (!doneEvent) {
      const chunk = await reader.read();
      if (options.signal.aborted) throw createAbortError();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) doneEvent = parseEvent(line, options) || doneEvent;
    }
    buffer += decoder.decode();
    if (buffer.trim()) doneEvent = parseEvent(buffer, options) || doneEvent;
    if (!doneEvent) throw new ProjectMasterProtocolError("The response ended unexpectedly.");
  } finally {
    if (options.signal.aborted) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function formatProjectMasterError(error: unknown): string {
  if (error instanceof ProjectMasterUnavailableError) return error.message;
  if (error instanceof ProjectMasterHttpError) {
    return `Project Master request failed (${error.status}): ${error.message}`;
  }
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong while talking to Project Master. Please retry.";
}
