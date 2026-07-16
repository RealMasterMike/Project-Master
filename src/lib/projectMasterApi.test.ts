import { beforeEach, describe, expect, it, vi } from "vitest";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: fetchMock }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import {
  API_BASE_URL,
  cancelChat,
  getCommunicationProfile,
  getConversation,
  listConversations,
  streamChat,
  submitCommunicationFeedback,
} from "./projectMasterApi";

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

  it("loads and validates saved conversations", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          conversations: [
            {
              id: "conversation-1",
              started_at: "2026-07-14T12:00:00Z",
              title: "First session",
              message_count: 2,
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "conversation-1",
          messages: [
            { role: "user", content: "Hello" },
            { role: "assistant", content: "Hi" },
          ],
        }),
        { status: 200 },
      ),
    );

    await expect(listConversations()).resolves.toEqual([
      {
        id: "conversation-1",
        startedAt: "2026-07-14T12:00:00Z",
        title: "First session",
        messageCount: 2,
      },
    ]);
    await expect(getConversation("conversation-1")).resolves.toEqual({
      id: "conversation-1",
      messages: [
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi" },
      ],
    });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${API_BASE_URL}/conversations?limit=50`,
      `${API_BASE_URL}/conversations/conversation-1`,
    ]);
  });

  it("loads the communication profile and saves explicit feedback", async () => {
    const profile = {
      preferences: [
        {
          key: "semantic_fidelity",
          value: "Preserve the user's actual meaning.",
          source: "explicit_user_instruction",
          confidence: 1,
          scope: "global",
          supporting_examples: [],
          status: "active",
        },
      ],
      disliked_response_patterns: ["unjustified assumptions"],
      corrections: [],
    };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200 }));
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          preference: {
            ...profile.preferences[0],
            source: "explicit_user_feedback",
            supporting_examples: ["Analyze before recommending."],
          },
          profile: {
            ...profile,
            corrections: [{ preference_key: "avoid_unsolicited_advice" }],
          },
        }),
        { status: 200 },
      ),
    );

    await expect(getCommunicationProfile()).resolves.toMatchObject({
      correctionsCount: 0,
      preferences: [{ key: "semantic_fidelity", source: "explicit_user_instruction" }],
    });
    await expect(
      submitCommunicationFeedback(
        "avoid_unsolicited_advice",
        "Analyze before recommending.",
      ),
    ).resolves.toMatchObject({ correctionsCount: 1 });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${API_BASE_URL}/profile/communication`,
      `${API_BASE_URL}/profile/communication/feedback`,
    ]);
    const [, feedbackRequest] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(feedbackRequest.body))).toEqual({
      category: "avoid_unsolicited_advice",
      note: "Analyze before recommending.",
      scope: "global",
    });
  });
});
