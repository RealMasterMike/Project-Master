import { beforeEach, describe, expect, it, vi } from "vitest";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: fetchMock }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { API_BASE_URL, cancelChat, streamChat } from "./projectMasterApi";

describe("Project Master stream cancellation protocol", () => {
  beforeEach(() => fetchMock.mockReset());

  it("includes the unique request ID in a streaming chat request", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        [
          JSON.stringify({ type: "start", conversation_id: "conversation-1" }),
          JSON.stringify({ type: "token", content: "hello" }),
          JSON.stringify({ type: "done", content: "hello" }),
          "",
        ].join("\n"),
        { status: 200 },
      ),
    );
    const tokens: string[] = [];

    await streamChat({
      requestId: "request-123",
      model: "test-model",
      message: "Hello",
      signal: new AbortController().signal,
      onToken: (token) => tokens.push(token),
      onConversation: vi.fn(),
    });

    expect(tokens).toEqual(["hello"]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/chat/stream`);
    expect(JSON.parse(String(init.body))).toMatchObject({ request_id: "request-123" });
  });

  it("sends a separate best-effort cancellation request", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ accepted: true, active: true }), { status: 200 }),
    );

    await cancelChat("request-123");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/chat/cancel`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ request_id: "request-123" });
  });
});
