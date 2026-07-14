import { invoke } from "@tauri-apps/api/core";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

export const API_BASE_URL = "http://127.0.0.1:8765/api/v1";
export const API_UNREACHABLE_MESSAGE =
  "Project Master backend is not reachable at 127.0.0.1:8765 — is it running?";

const STATUS_TIMEOUT_MS = 8_000;

export interface ProjectMasterModel { name: string; }

interface ModelStatus {
  configured_model: string;
  num_ctx: number;
  ollama_reachable: boolean;
  models: string[];
}

interface StreamChatOptions {
  requestId: string;
  model: string;
  message: string;
  conversationId?: string;
  signal: AbortSignal;
  onToken: (token: string) => void;
  onConversation: (conversationId: string) => void;
}

interface StreamEvent {
  type?: unknown;
  content?: unknown;
  conversation_id?: unknown;
  error?: unknown;
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
}

export async function ensureManagedBackend(): Promise<void> {
  if (!("__TAURI_INTERNALS__" in window)) return;

  try {
    const status = await invoke<ManagedBackendStatus>("ensure_backend");
    if (!status.ready) {
      throw new Error("The packaged Project Master backend did not report ready.");
    }
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
    return await tauriFetch(`${API_BASE_URL}${path}`, init);
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
}> {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort, { once: true });
  const timeout = window.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);
  try {
    const response = await request("/models/status", { signal: controller.signal });
    await ensureSuccess(response);
    const status = (await response.json()) as ModelStatus;
    if (!Array.isArray(status.models) || typeof status.configured_model !== "string") {
      throw new ProjectMasterProtocolError("Project Master returned an invalid model status.");
    }
    return {
      models: status.models.map((name) => ({ name })),
      configuredModel: status.configured_model,
      contextLength: status.num_ctx,
      ollamaReachable: status.ollama_reachable,
    };
  } catch (error) {
    if (signal?.aborted) throw createAbortError();
    if (controller.signal.aborted) throw new ProjectMasterUnavailableError();
    throw error;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", forwardAbort);
  }
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
  if (event.type === "token" && typeof event.content === "string") {
    options.onToken(event.content);
  }
  if (event.type === "error") {
    throw new Error(typeof event.error === "string" ? event.error : "Backend stream failed.");
  }
  if (event.type === "cancelled") throw createAbortError();
  return event.type === "done";
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
