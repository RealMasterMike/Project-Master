import { beforeEach, describe, expect, it, vi } from "vitest";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: fetchMock }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import {
  API_BASE_URL,
  cancelChat,
  createComfyJob,
  createVoiceJob,
  decideDreamItem,
  getComfyOverview,
  getComfyArtifactContent,
  getCommunicationProfile,
  getConversation,
  getDreamOverview,
  getModelStatus,
  getRunDetail,
  getVoiceEngineHealth,
  getVoiceOverview,
  importComfyWorkflow,
  importVoiceReference,
  indexProjectKnowledge,
  listConversations,
  listApprovals,
  listProjectKnowledge,
  listProjects,
  resolveApproval,
  runManualDream,
  saveComfyProfile,
  saveDreamRecipe,
  saveDreamSchedule,
  saveDesignedVoiceProfile,
  saveReferenceVoiceProfile,
  setDreamScheduleEnabled,
  setProjectDreaming,
  deleteDreamSchedule,
  searchProjectKnowledge,
  streamChat,
  submitCommunicationFeedback,
  type ProjectMasterRunActivity,
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
      mode: "team",
      allowMutations: true,
      projectId: "project-1",
      signal: new AbortController().signal,
      onToken: (token) => tokens.push(token),
      onConversation: vi.fn(),
    });

    expect(tokens).toEqual(["hello"]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/chat/stream`);
    expect(JSON.parse(String(init.body))).toMatchObject({
      request_id: "request-123",
      mode: "team",
      allow_mutations: true,
      project_id: "project-1",
    });
  });

  it("sends an explicit read-only mutation policy in Direct mode", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        [
          JSON.stringify({ type: "start", conversation_id: "conversation-1" }),
          JSON.stringify({ type: "token", content: "Read-only answer" }),
          JSON.stringify({ type: "done", content: "Read-only answer" }),
          "",
        ].join("\n"),
        { status: 200 },
      ),
    );

    await streamChat({
      requestId: "request-readonly",
      model: "test-model",
      message: "Inspect this project",
      mode: "direct",
      allowMutations: false,
      signal: new AbortController().signal,
      onToken: vi.fn(),
      onConversation: vi.fn(),
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      request_id: "request-readonly",
      mode: "direct",
      allow_mutations: false,
    });
  });

  it("surfaces bounded redacted tool details without specialist drafts", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        [
          JSON.stringify({ type: "start", conversation_id: "conversation-1" }),
          JSON.stringify({
            type: "team",
            run_id: "run-123",
            activity: {
              type: "worker_completed",
              message: "Critic completed",
              member: { model: "critic-model", role: "critic" },
              worker: { output: "private specialist draft" },
              result: { final: "private synthesis" },
            },
          }),
          JSON.stringify({
            type: "tool",
            run_id: "run-123",
            tool: {
              name: "calculator",
              arguments: {
                expression: "2 + 2",
                api_key: "input-secret",
              },
              result: JSON.stringify({
                answer: 4,
                token: "output-secret",
              }),
              ok: true,
            },
          }),
          JSON.stringify({ type: "token", content: "Safe answer" }),
          JSON.stringify({ type: "done", content: "Safe answer", run_id: "run-123" }),
          "",
        ].join("\n"),
        { status: 200 },
      ),
    );
    const activities: ProjectMasterRunActivity[] = [];
    const runs: string[] = [];

    await streamChat({
      requestId: "request-team",
      model: "lead-model",
      message: "Solve this",
      mode: "team",
      allowMutations: false,
      signal: new AbortController().signal,
      onToken: vi.fn(),
      onConversation: vi.fn(),
      onActivity: (activity) => activities.push(activity),
      onRun: (runId) => runs.push(runId),
    });

    expect(activities).toEqual([
      {
        kind: "worker_completed",
        message: "Critic completed",
        runId: "run-123",
        model: "critic-model",
        role: "critic",
      },
      {
        kind: "tool_completed",
        message: "calculator completed",
        runId: "run-123",
        tool: "calculator",
        ok: true,
        inputDetail:
          '{\n  "expression": "2 + 2",\n  "api_key": "[redacted]"\n}',
        outputDetail:
          '{\n  "answer": 4,\n  "token": "[redacted]"\n}',
      },
    ]);
    expect(JSON.stringify(activities)).not.toContain("private");
    expect(JSON.stringify(activities)).not.toContain("input-secret");
    expect(JSON.stringify(activities)).not.toContain("output-secret");
    expect(runs).toEqual(["run-123", "run-123", "run-123"]);
  });

  it("parses the physical team catalog and reports team compatibility", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          configured_model: "lead-model",
          num_ctx: 32768,
          ollama_reachable: true,
          models: ["lead-model", "critic-model"],
          catalog: [
            {
              physical_id: "sha256:model-1",
              primary_tag: "lead-model",
              tags: ["lead-model", "lead-alias"],
              capabilities: ["completion", "tools"],
              size_bytes: 1_024,
            },
          ],
        }),
        { status: 200 },
      ),
    );

    await expect(getModelStatus()).resolves.toMatchObject({
      teamAvailable: true,
      teamCatalog: [
        {
          physicalId: "sha256:model-1",
          primaryTag: "lead-model",
          tags: ["lead-model", "lead-alias"],
          capabilities: ["completion", "tools"],
          sizeBytes: 1_024,
        },
      ],
    });
  });

  it("parses project, run, and approval metadata without arbitrary payload data", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          projects: [
            {
              id: "project-1",
              name: "Daily driver",
              description: "Local work",
              status: "active",
              root_path: "/workspace",
              updated_at: "2026-07-27T10:00:00Z",
              metadata: {
                hidden: "not surfaced",
                allow_dreaming: true,
              },
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          run: {
            id: "run-1",
            project_id: "project-1",
            kind: "team_chat",
            objective: "Ship safely",
            mode: "team",
            status: "complete",
            created_at: "2026-07-27T10:00:00Z",
          },
          events: [
            {
              id: 1,
              event_type: "worker_completed",
              summary: "Critic completed",
              created_at: "2026-07-27T10:01:00Z",
              payload: { private_worker_draft: "must never surface" },
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          approvals: [
            {
              id: "approval-1",
              run_id: "run-1",
              action_kind: "write_file",
              target: "README.md",
              request: { private_argument: "not surfaced" },
              risk: "medium",
              reversible: 1,
              rollback_plan: "Restore prior content",
              status: "pending",
              created_at: "2026-07-27T10:02:00Z",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ id: "approval-1", status: "approved", note: "Reviewed" }),
        { status: 200 },
      ),
    );

    const projects = await listProjects();
    const detail = await getRunDetail("run-1");
    const approvals = await listApprovals();
    await resolveApproval("approval-1", "approved", "Reviewed");

    expect(projects).toEqual([
      {
        id: "project-1",
        name: "Daily driver",
        description: "Local work",
        status: "active",
        rootPath: "/workspace",
        allowDreaming: true,
        updatedAt: "2026-07-27T10:00:00Z",
      },
    ]);
    expect(detail.events).toEqual([
      {
        id: 1,
        type: "worker_completed",
        summary: "Critic completed",
        createdAt: "2026-07-27T10:01:00Z",
      },
    ]);
    expect(approvals[0]).not.toHaveProperty("request");
    expect(JSON.stringify({ projects, detail, approvals })).not.toContain("private");
    const [approvalUrl, approvalInit] = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(approvalUrl).toBe(`${API_BASE_URL}/approvals/approval-1/resolve`);
    expect(JSON.parse(String(approvalInit.body))).toEqual({
      status: "approved",
      note: "Reviewed",
    });
  });

  it("indexes and searches Project Binder documents with versioned citations", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          documents: [
            {
              id: "doc-1-v2",
              project_id: "project-1",
              root_path: "/home/private/project",
              relative_path: "docs/plan.md",
              content_sha256: "a".repeat(64),
              version: 2,
              mime_type: "text/markdown",
              size_bytes: 4096,
              indexed_at: "2026-07-27T10:00:00Z",
              active: true,
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          project_id: "project-1",
          root_path: "/home/private/project",
          indexed: 1,
          unchanged: 2,
          skipped: 1,
          archived: 0,
          errors: [],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          query: "release gate",
          results: [
            {
              chunk_id: "chunk-1",
              document_id: "doc-1-v2",
              project_id: "project-1",
              relative_path: "docs/plan.md",
              line_start: 12,
              line_end: 18,
              content: "Run the full release gate.",
              score: 0.91,
              citation: "docs/plan.md:12-18",
              content_sha256: "a".repeat(64),
              document_version: 2,
            },
          ],
        }),
        { status: 200 },
      ),
    );

    const documents = await listProjectKnowledge("project-1");
    const indexed = await indexProjectKnowledge("project-1", "docs");
    const hits = await searchProjectKnowledge("project-1", "release gate");

    expect(documents).toMatchObject([
      {
        relativePath: "docs/plan.md",
        version: 2,
        active: true,
      },
    ]);
    expect(JSON.stringify(documents)).not.toContain("/home/private");
    expect(indexed).toEqual({
      projectId: "project-1",
      indexed: 1,
      unchanged: 2,
      skipped: 1,
      archived: 0,
      errorCount: 0,
    });
    expect(hits).toMatchObject([
      {
        citation: "docs/plan.md:12-18",
        documentVersion: 2,
        excerpt: "Run the full release gate.",
      },
    ]);
    const [, indexInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(indexInit.body))).toEqual({
      relative_path: "docs",
      prune: true,
    });
  });

  it("records explicit future scheduled Dream consent on one project", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "project-1",
          name: "Daily driver",
          description: "Local work",
          status: "active",
          root_path: "/workspace",
          updated_at: "2026-07-27T11:00:00Z",
          metadata: {
            allow_dreaming: true,
            unrelated_setting: "preserved",
          },
        }),
        { status: 200 },
      ),
    );

    await expect(setProjectDreaming("project-1", true)).resolves.toMatchObject({
      id: "project-1",
      allowDreaming: true,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/projects/project-1/dreaming`);
    expect(JSON.parse(String(init.body))).toEqual({ enabled: true });
  });

  it("saves one explicit project scope on a scheduled Dream recipe", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          recipe_id: "nightly-project",
          name: "Nightly Project",
          kind: "custom",
          objective: "Review consented Binder excerpts",
          source_scopes: ["project:project-1"],
          version: 1,
        }),
        { status: 201 },
      ),
    );

    await expect(
      saveDreamRecipe({
        recipeId: "nightly-project",
        name: "Nightly Project",
        objective: "Review consented Binder excerpts",
        sourceScopes: ["project:project-1"],
      }),
    ).resolves.toMatchObject({
      recipeId: "nightly-project",
      sourceScopes: ["project:project-1"],
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      recipe_id: "nightly-project",
      source_scopes: ["project:project-1"],
    });
  });

  it("uses the explicit Dream proposal and decision endpoints", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          proposal_only: true,
          scheduled_execution_enabled: true,
          background_configured: true,
          recipes: [
            {
              recipe_id: "idea-garden",
              name: "Idea Garden",
              kind: "idea_garden",
              objective: "Find opportunities",
              source_scopes: [],
              version: 1,
            },
            {
              recipe_id: "nightly-project",
              name: "Nightly Project",
              kind: "custom",
              objective: "Review consented Binder excerpts",
              source_scopes: ["project:project-1"],
              version: 1,
            },
          ],
          schedules: [
            {
              schedule_id: "nightly-ideas",
              recipe_id: "nightly-project",
              timezone: "America/New_York",
              local_time: "02:30:00",
              enabled: true,
              catch_up: "latest",
              on_time_grace_seconds: 900,
              max_lookback_days: 7,
              max_catch_up_windows: 3,
              resource_rules: {
                min_idle_seconds: 300,
                max_cpu_percent: 60,
                min_available_memory_bytes: 2147483648,
                min_gpu_free_bytes: null,
                require_no_model_jobs: true,
                require_ac_power: false,
              },
              quiet_window: null,
              version: 2,
              updated_at_utc: "2026-07-27T10:00:00Z",
            },
          ],
          runs: [],
          inbox: [
            {
              item_id: "dream-1",
              recipe_id: "idea-garden",
              proposal_text: "Try a reversible experiment.",
              epistemic_label: "speculation",
              source_refs: ["note-1"],
              disposition: "pending",
              created_at_utc: "2026-07-27T10:00:00Z",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ run: {} }), { status: 200 }));
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ item: {} }), { status: 200 }));
    const storedSchedule = {
      schedule_id: "nightly-ideas",
      recipe_id: "nightly-project",
      timezone: "America/New_York",
      local_time: "02:30:00",
      enabled: true,
      catch_up: "latest",
      on_time_grace_seconds: 900,
      max_lookback_days: 7,
      max_catch_up_windows: 3,
      resource_rules: {
        min_idle_seconds: 300,
        max_cpu_percent: 60,
        min_available_memory_bytes: 2147483648,
        min_gpu_free_bytes: null,
        require_no_model_jobs: true,
        require_ac_power: false,
      },
      quiet_window: null,
      version: 3,
      updated_at_utc: "2026-07-27T11:00:00Z",
    };
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(storedSchedule), { status: 201 }),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ...storedSchedule, enabled: false }), {
        status: 200,
      }),
    );
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(getDreamOverview()).resolves.toMatchObject({
      proposalOnly: true,
      scheduledExecutionEnabled: true,
      backgroundConfigured: true,
      schedules: [
        {
          scheduleId: "nightly-ideas",
          localTime: "02:30:00",
          resourceRules: { requireNoModelJobs: true },
        },
      ],
      recipes: [
        { recipeId: "idea-garden", sourceScopes: [] },
        {
          recipeId: "nightly-project",
          sourceScopes: ["project:project-1"],
        },
      ],
      inbox: [{ epistemicLabel: "speculation", sourceRefs: ["note-1"] }],
    });
    await runManualDream({
      recipeId: "idea-garden",
      sourceId: "note-1",
      locator: "manual-note",
      content: "Explicit source",
    });
    await decideDreamItem(
      "dream-1",
      "promote",
      "Worth a bounded prototype.",
    );
    await saveDreamSchedule({
      scheduleId: "nightly-ideas",
      recipeId: "nightly-project",
      timezone: "America/New_York",
      localTime: "02:30",
      enabled: true,
      catchUp: "latest",
      onTimeGraceSeconds: 900,
      maxLookbackDays: 7,
      maxCatchUpWindows: 3,
      resourceRules: {
        minIdleSeconds: 300,
        maxCpuPercent: 60,
        minAvailableMemoryBytes: 2147483648,
        requireNoModelJobs: true,
        requireAcPower: false,
      },
      expectedVersion: 2,
    });
    await setDreamScheduleEnabled("nightly-ideas", false);
    await deleteDreamSchedule("nightly-ideas");

    const [, runInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(runInit.body))).toMatchObject({
      recipe_id: "idea-garden",
      sources: [
        {
          source_id: "note-1",
          content: "Explicit source",
          allow_dreaming: true,
        },
      ],
    });
    const [decisionUrl, decisionInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(decisionUrl).toBe(`${API_BASE_URL}/dreams/inbox/dream-1/promote`);
    expect(JSON.parse(String(decisionInit.body))).toEqual({
      rationale: "Worth a bounded prototype.",
      target: "project_idea_candidate",
    });
    const [scheduleUrl, scheduleInit] = fetchMock.mock.calls[3] as [
      string,
      RequestInit,
    ];
    expect(scheduleUrl).toBe(`${API_BASE_URL}/dreams/schedules`);
    expect(JSON.parse(String(scheduleInit.body))).toMatchObject({
      schedule_id: "nightly-ideas",
      expected_version: 2,
      resource_rules: {
        require_no_model_jobs: true,
        min_idle_seconds: 300,
      },
    });
    expect(fetchMock.mock.calls[4][0]).toBe(
      `${API_BASE_URL}/dreams/schedules/nightly-ideas/enabled`,
    );
    expect(fetchMock.mock.calls[5][0]).toBe(
      `${API_BASE_URL}/dreams/schedules/nightly-ideas`,
    );
    expect((fetchMock.mock.calls[5][1] as RequestInit).method).toBe("DELETE");
  });

  it("parses ComfyUI metadata and queues only explicit profile/workflow IDs", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          support_available: true,
          profiles: [
            {
              id: "local",
              name: "Local",
              base_url: "http://127.0.0.1:8188",
              verify_tls: true,
              auth: { secret_ref: { key: "SECRET_VALUE" } },
            },
          ],
          workflows: [
            {
              revision: {
                id: "comfy-wf-1",
                name: "Portrait",
                digest: "abc123",
                created_at: "2026-07-27T10:00:00Z",
                workflow: { "1": { inputs: { prompt: "private prompt" } } },
                bindings: [
                  {
                    id: "prompt",
                    node_id: "1",
                    input_name: "prompt",
                    value_type: "string",
                    required: true,
                    default_value: null,
                    minimum: null,
                    maximum: null,
                    choices: [],
                    description: "Positive prompt",
                  },
                  {
                    id: "seed",
                    node_id: "2",
                    input_name: "seed",
                    value_type: "integer",
                    required: true,
                    default_value: 0,
                    minimum: 0,
                    maximum: 4294967295,
                    choices: [],
                    description: "Generation seed",
                  },
                ],
              },
              trust_state: "approved",
            },
          ],
          jobs: [
            {
              id: "comfy-job-1",
              profile_id: "local",
              workflow_revision_id: "comfy-wf-1",
              status: "succeeded",
              created_at: "2026-07-27T10:02:00Z",
              artifact_status: "partial",
              artifact_error: "One optional preview could not be imported.",
              artifacts: [
                {
                  id: `comfy-artifact-${"a".repeat(40)}`,
                  sha256: "b".repeat(64),
                  size_bytes: 7,
                  media_type: "image/png",
                  original_filename: "portrait.png",
                  relative_path: `jobs/comfy-job-1/comfy-artifact-${"a".repeat(40)}.png`,
                  created_at: "2026-07-27T10:03:00Z",
                  verified: true,
                  provenance: {
                    job_id: "comfy-job-1",
                    profile_id: "local",
                    workflow_revision_id: "comfy-wf-1",
                    workflow_digest: "c".repeat(64),
                    remote_prompt_id: "prompt-1",
                    output: {
                      node_id: "9",
                      category: "images",
                      output_index: 0,
                      ref: {
                        filename: "portrait.png",
                        subfolder: "",
                        type: "output",
                      },
                    },
                    history_sha256: "d".repeat(64),
                    source_base_url: "http://127.0.0.1:8188",
                    source_url:
                      "http://127.0.0.1:8188/view?filename=portrait.png&subfolder=&type=output",
                    fetched_at: "2026-07-27T10:03:00Z",
                  },
                },
              ],
            },
          ],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1" }), { status: 202 }));
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          revision: {
            id: "comfy-wf-2",
            name: "Bound portrait",
            digest: "def456",
            created_at: "2026-07-27T10:01:00Z",
            bindings: [
              {
                id: "prompt",
                node_id: "1",
                input_name: "prompt",
                value_type: "string",
                required: true,
                default_value: null,
                minimum: null,
                maximum: null,
                choices: [],
                description: "Positive prompt",
              },
            ],
          },
          trust_state: "pending",
        }),
        { status: 201 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(new Blob(["PNGDATA"], { type: "image/png" }), {
        status: 200,
        headers: { "Content-Type": "image/png" },
      }),
    );

    const overview = await getComfyOverview();
    expect(overview).toMatchObject({
      profiles: [{ id: "local", baseUrl: "http://127.0.0.1:8188" }],
      workflows: [
        {
          id: "comfy-wf-1",
          trustState: "approved",
          bindings: [
            { id: "prompt", valueType: "string" },
            { id: "seed", minimum: 0 },
          ],
        },
      ],
      jobs: [
        {
          id: "comfy-job-1",
          artifactStatus: "partial",
          artifactError: "One optional preview could not be imported.",
          artifacts: [
            {
              originalFilename: "portrait.png",
              mediaType: "image/png",
              sizeBytes: 7,
              verified: true,
              provenance: {
                nodeId: "9",
                category: "images",
                remotePromptId: "prompt-1",
              },
            },
          ],
        },
      ],
    });
    expect(JSON.stringify(overview)).not.toContain("SECRET_VALUE");
    expect(JSON.stringify(overview)).not.toContain("private prompt");
    expect(JSON.stringify(overview)).not.toContain("source_url");
    expect(JSON.stringify(overview)).not.toContain("/view?");

    await createComfyJob({
      profileId: "local",
      workflowRevisionId: "comfy-wf-1",
      values: { prompt: "A gold robot", seed: 42 },
    });
    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      profile_id: "local",
      workflow_revision_id: "comfy-wf-1",
      values: { prompt: "A gold robot", seed: 42 },
    });
    await importComfyWorkflow(
      "Bound portrait",
      { "1": { class_type: "CLIPTextEncode", inputs: { prompt: "" } } },
      [
        {
          id: "prompt",
          nodeId: "1",
          inputName: "prompt",
          valueType: "string",
          required: true,
          choices: [],
          description: "Positive prompt",
        },
      ],
    );
    const [, importInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(JSON.parse(String(importInit.body))).toMatchObject({
      bindings: [
        {
          id: "prompt",
          node_id: "1",
          input_name: "prompt",
          value_type: "string",
        },
      ],
    });
    const content = await getComfyArtifactContent(
      "comfy-job-1",
      `comfy-artifact-${"a".repeat(40)}`,
    );
    expect(await content.text()).toBe("PNGDATA");
    expect(fetchMock.mock.calls[3][0]).toBe(
      `${API_BASE_URL}/integrations/comfyui/jobs/comfy-job-1/artifacts/comfy-artifact-${"a".repeat(40)}/content`,
    );
  });

  it("sends explicit trusted hosts when saving a remote ComfyUI profile", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "remote",
          name: "Remote ComfyUI",
          base_url: "https://comfy.example.test",
          verify_tls: true,
        }),
        { status: 201 },
      ),
    );

    await saveComfyProfile({
      id: "remote",
      name: "Remote ComfyUI",
      baseUrl: "https://comfy.example.test",
      trustedHosts: ["comfy.example.test"],
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/integrations/comfyui/profiles`);
    expect(JSON.parse(String(init.body))).toEqual({
      id: "remote",
      name: "Remote ComfyUI",
      base_url: "https://comfy.example.test",
      trusted_hosts: ["comfy.example.test"],
      verify_tls: true,
    });
  });

  it("parses voice inventory and sends rights-aware profile and render requests", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          support_available: true,
          profiles: [],
          projects: [],
          installed_packs: [],
          optional_pack_templates: [
            {
              id: "qwen3-tts-local",
              display_name: "Qwen3-TTS Local",
              installed: false,
              capabilities: ["voice_design"],
              upstream_homepage: "https://example.test",
            },
          ],
          jobs: [],
          artifacts: [
            {
              id: "artifact-1",
              media_type: "audio/wav",
              format: "wav",
              size_bytes: 2048,
              duration_seconds: 1.5,
              created_at: "2026-07-27T10:00:00Z",
              provenance: { text: "not surfaced" },
            },
          ],
          references: [],
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          available: true,
          status: "ready",
          detail: "Local engine is ready.",
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: "voice-1" }), { status: 201 }));
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1" }), { status: 201 }));

    await expect(getVoiceOverview()).resolves.toMatchObject({
      packs: [{ id: "qwen3-tts-local", installed: false }],
      artifacts: [{ id: "artifact-1", durationSeconds: 1.5 }],
    });
    await expect(getVoiceEngineHealth("qwen-pack")).resolves.toEqual({
      available: true,
      status: "ready",
      detail: "Local engine is ready.",
    });
    await saveDesignedVoiceProfile({
      profileId: "voice-1",
      name: "Studio",
      language: "en-US",
      description: "Calm synthetic narrator",
      publication: true,
    });
    await createVoiceJob({
      projectId: "script-1",
      packId: "qwen-pack",
      purpose: "publication",
    });

    const [, profileInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(JSON.parse(String(profileInit.body))).toMatchObject({
      scopes: ["voice_generation", "publication"],
      attested_by_user: true,
    });
    const [, jobInit] = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(JSON.parse(String(jobInit.body))).toEqual({
      project_id: "script-1",
      engine_pack_id: "qwen-pack",
      purpose: "publication",
    });
  });

  it("imports a local WAV and creates an explicitly scoped reference voice", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          artifact_id: "voice-reference-1",
          sha256: "b".repeat(64),
          media_type: "audio/wav",
          duration_seconds: 2.5,
          sample_rate_hz: 24000,
          channels: 1,
          transcript: "Hello",
        }),
        { status: 201 },
      ),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: "reference-profile-1" }), { status: 201 }),
    );
    const bytes = new Uint8Array(44);
    bytes.set([82, 73, 70, 70], 0);

    await expect(
      importVoiceReference(
        {
          name: "voice.wav",
          type: "audio/wav",
          size: bytes.byteLength,
          arrayBuffer: async () => bytes.buffer,
        },
        "Hello",
      ),
    ).resolves.toMatchObject({
      artifactId: "voice-reference-1",
      mediaType: "audio/wav",
    });
    await saveReferenceVoiceProfile({
      profileId: "reference-profile-1",
      name: "Licensed narrator",
      language: "en-US",
      description: "Attested local voice",
      referenceArtifactId: "voice-reference-1",
      rightsBasis: "licensed_voice",
      subjectLabel: "Licensed narrator",
      publication: true,
      commercial: true,
      notes: "License reviewed locally.",
      evidenceArtifactIds: ["consent-proof-1"],
    });

    const [, importInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const importBody = JSON.parse(String(importInit.body));
    expect(importBody.file_name).toBe("voice.wav");
    expect(importBody.audio_base64).toBeTypeOf("string");
    expect(importBody).not.toHaveProperty("path");
    const [, profileInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(profileInit.body))).toMatchObject({
      reference_artifact_ids: ["voice-reference-1"],
      rights_basis: "licensed_voice",
      scopes: ["voice_generation", "publication", "commercial_use"],
      attested_by_user: true,
      evidence_artifact_ids: ["consent-proof-1"],
    });
  });

  it("classifies generated WAV references without consent/license evidence", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: "synthetic-reference-1" }), {
        status: 201,
      }),
    );

    await saveReferenceVoiceProfile({
      profileId: "synthetic-reference-1",
      name: "Generated reference",
      language: "en-US",
      description: "No real person is represented.",
      referenceArtifactId: "voice-reference-generated",
      rightsBasis: "synthetic_reference",
      subjectLabel: "Synthetic generated voice",
      publication: false,
      commercial: false,
      notes: "Generated locally.",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      rights_basis: "synthetic_reference",
      evidence_artifact_ids: [],
      attested_by_user: true,
    });
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
