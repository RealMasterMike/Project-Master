import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  cancelComfyJob,
  cancelVoiceJob,
  createComfyJob,
  createProject,
  createVoiceJob,
  deleteDreamSchedule,
  decideComfyWorkflow,
  decideDreamItem,
  formatProjectMasterError,
  getComfyOverview,
  getComfyArtifactContent,
  getComfyProfileStatus,
  getDreamOverview,
  getProjectRuns,
  getRunDetail,
  getToolStatus,
  getVoiceEngineHealth,
  getVoiceArtifactContent,
  getVoiceOverview,
  importComfyWorkflow,
  importVoiceReference,
  indexProjectKnowledge,
  listApprovals,
  listProjectKnowledge,
  listProjects,
  resolveApproval,
  runManualDream,
  runVoiceJob,
  saveComfyProfile,
  saveDesignedVoiceProfile,
  saveDreamRecipe,
  saveDreamSchedule,
  saveReferenceVoiceProfile,
  saveVoiceProject,
  searchProjectKnowledge,
  setDreamScheduleEnabled,
  setProjectDreaming,
  type ComfyOverview,
  type ComfyArtifactSummary,
  type ComfyWorkflowBinding,
  type DreamOverview,
  type DreamRecipeSummary,
  type DreamScheduleSummary,
  type MasterApproval,
  type MasterProject,
  type MasterRun,
  type MasterRunEvent,
  type KnowledgeDocumentSummary,
  type KnowledgeSearchHit,
  type VoiceOverview,
  type VoiceEngineHealth,
} from "../lib/projectMasterApi";
import type { MasterWorkspace } from "./WorkspaceNavigation";

interface DashboardFrameProps {
  eyebrow: string;
  title: string;
  description: string;
  status: string;
  children: ReactNode;
  error?: string | null;
  busy?: boolean;
  onRefresh: () => void;
}

function DashboardFrame({
  eyebrow,
  title,
  description,
  status,
  children,
  error,
  busy,
  onRefresh,
}: DashboardFrameProps) {
  return (
    <section className="feature-workspace feature-workspace--dashboard">
      <div className="feature-workspace__copy">
        <span className="feature-workspace__eyebrow">{eyebrow}</span>
        <div className="feature-workspace__heading">
          <div>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          <button
            className="feature-status feature-status--ready"
            type="button"
            onClick={onRefresh}
            disabled={busy}
          >
            {busy ? "Working…" : status}
          </button>
        </div>
        {error ? <div className="dashboard-alert" role="alert">{error}</div> : null}
        <div className="dashboard-grid">{children}</div>
      </div>
    </section>
  );
}

function Panel({
  title,
  kicker,
  children,
  wide = false,
}: {
  title: string;
  kicker: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <section className={`dashboard-panel ${wide ? "dashboard-panel--wide" : ""}`}>
      <header>
        <span>{kicker}</span>
        <h2>{title}</h2>
      </header>
      <div className="dashboard-panel__body">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="dashboard-empty">{children}</p>;
}

function Stamp({ value }: { value?: string }) {
  if (!value) return null;
  const date = new Date(value);
  return <time>{Number.isNaN(date.valueOf()) ? value : date.toLocaleString()}</time>;
}

function useBusyAction(refresh: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = useCallback(
    async (operation: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await operation();
        await refresh();
      } catch (caught) {
        setError(formatProjectMasterError(caught));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );
  return { busy, error, setError, act };
}

function ProjectsDashboard({
  selectedProject,
  onSelectProject,
  onProjectsChange,
}: {
  selectedProject: string;
  onSelectProject: (projectId: string) => void;
  onProjectsChange: (projects: MasterProject[]) => void;
}) {
  const [projects, setProjects] = useState<MasterProject[]>([]);
  const [runs, setRuns] = useState<MasterRun[]>([]);
  const [events, setEvents] = useState<MasterRunEvent[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [tools, setTools] = useState<Array<{ name: string; enabled: boolean }>>([]);
  const [writesEnabled, setWritesEnabled] = useState(false);
  const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
  const [searchHits, setSearchHits] = useState<KnowledgeSearchHit[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [indexPath, setIndexPath] = useState(".");
  const [indexSummary, setIndexSummary] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [projectRoot, setProjectRoot] = useState("");

  const refresh = useCallback(async () => {
    const [nextProjects, toolStatus] = await Promise.all([
      listProjects(),
      getToolStatus(),
    ]);
    setProjects(nextProjects);
    onProjectsChange(nextProjects);
    setTools(toolStatus.tools);
    setWritesEnabled(toolStatus.writesEnabled);
    if (
      selectedProject &&
      !nextProjects.some((item) => item.id === selectedProject)
    ) {
      onSelectProject("");
    }
  }, [onProjectsChange, onSelectProject, selectedProject]);
  const action = useBusyAction(refresh);
  const activeProject = projects.find(
    (project) => project.id === selectedProject,
  );

  useEffect(() => {
    void action.act(async () => undefined);
    // Initial load is intentionally tied only to the stable loader.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  useEffect(() => {
    if (!selectedProject) {
      setRuns([]);
      setDocuments([]);
      return;
    }
    void Promise.all([
      getProjectRuns(selectedProject),
      listProjectKnowledge(selectedProject),
    ])
      .then(([nextRuns, nextDocuments]) => {
        setRuns(nextRuns);
        setDocuments(nextDocuments);
        setSelectedRun((current) =>
          nextRuns.some((item) => item.id === current)
            ? current
            : nextRuns[0]?.id ?? "",
        );
      })
      .catch((caught) => action.setError(formatProjectMasterError(caught)));
  }, [action.setError, selectedProject]);

  useEffect(() => {
    if (!selectedRun) {
      setEvents([]);
      return;
    }
    void getRunDetail(selectedRun)
      .then((detail) => setEvents(detail.events))
      .catch((caught) => action.setError(formatProjectMasterError(caught)));
  }, [action.setError, selectedRun]);

  function submitProject(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      const created = await createProject({
        name: name.trim(),
        description: description.trim(),
        rootPath: projectRoot.trim() || undefined,
      });
      onSelectProject(created.id);
      setName("");
      setDescription("");
      setProjectRoot("");
    });
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      setSearchHits(await searchProjectKnowledge(selectedProject, searchQuery.trim()));
    });
  }

  function runIndex() {
    void action.act(async () => {
      const result = await indexProjectKnowledge(selectedProject, indexPath);
      setIndexSummary(
        `${result.indexed} indexed · ${result.unchanged} unchanged · ${result.archived} archived · ${result.errorCount} errors`,
      );
    });
  }

  return (
    <DashboardFrame
      eyebrow="DURABLE WORK // RUN HISTORY"
      title="Projects"
      description="Inspect durable objectives, run states, and metadata-only event history."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Project index" kicker={`${projects.length} LOCAL`}>
        <form className="compact-form" onSubmit={submitProject}>
          <input value={name} onChange={(event) => setName(event.currentTarget.value)} placeholder="Project name" required />
          <input value={projectRoot} onChange={(event) => setProjectRoot(event.currentTarget.value)} placeholder="Local project root (required for Binder)" />
          <textarea value={description} onChange={(event) => setDescription(event.currentTarget.value)} placeholder="Description" rows={2} />
          <button className="button button--secondary" disabled={action.busy}>Create project</button>
        </form>
        <div className="dashboard-list">
          {projects.map((project) => (
            <button
              className={project.id === selectedProject ? "is-active" : ""}
              type="button"
              key={project.id}
              onClick={() => onSelectProject(project.id)}
            >
              <strong>{project.name}</strong>
              <span>{project.status} · {project.description || "No description"}</span>
            </button>
          ))}
        </div>
      </Panel>
      <Panel title="Project Binder" kicker={`${documents.length} VERSIONS`} wide>
        {selectedProject ? (
          <>
            {activeProject ? (
              <div
                className={`dream-source-consent ${
                  activeProject.allowDreaming ? "is-enabled" : ""
                }`}
              >
                <div>
                  <strong>Scheduled Dream source consent</strong>
                  <p>
                    {activeProject.allowDreaming
                      ? "Allowed: future scheduled Dream runs may use local excerpts already indexed in this Project Binder. This does not grant raw filesystem access or automatic promotion."
                      : "Not allowed: scheduled Dream runs cannot use this Project Binder. Enable only if future scheduled runs may use excerpts you have already indexed here."}
                  </p>
                </div>
                <button
                  className={`button ${
                    activeProject.allowDreaming
                      ? "button--secondary"
                      : "button--primary"
                  }`}
                  type="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action.act(() =>
                      setProjectDreaming(
                        activeProject.id,
                        !activeProject.allowDreaming,
                      ),
                    )
                  }
                >
                  {activeProject.allowDreaming
                    ? "Revoke scheduled use"
                    : "Allow future scheduled use"}
                </button>
              </div>
            ) : null}
            <div className="binder-toolbar">
              <label>
                Relative folder
                <input
                  value={indexPath}
                  onChange={(event) => setIndexPath(event.currentTarget.value)}
                  placeholder="."
                />
              </label>
              <button
                className="button button--secondary"
                type="button"
                disabled={action.busy}
                onClick={runIndex}
              >
                Index local documents
              </button>
              {indexSummary ? <span>{indexSummary}</span> : null}
            </div>
            <form className="binder-search" onSubmit={submitSearch}>
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.currentTarget.value)}
                placeholder="Search indexed project knowledge"
                required
              />
              <button className="button button--primary" disabled={action.busy}>
                Search
              </button>
            </form>
            {searchHits.length ? (
              <div className="citation-list">
                {searchHits.map((hit) => (
                  <article key={`${hit.documentId}-${hit.citation}`}>
                    <header>
                      <code>{hit.citation}</code>
                      <span>v{hit.documentVersion} · score {hit.score.toFixed(3)}</span>
                    </header>
                    <p>{hit.excerpt}</p>
                    <small>SHA-256 {hit.sha256.slice(0, 16)}…</small>
                  </article>
                ))}
              </div>
            ) : null}
            <div className="document-table">
              {documents.map((document) => (
                <div key={document.id}>
                  <strong>{document.relativePath}</strong>
                  <span>
                    v{document.version} · {document.active ? "active" : "archived"} ·{" "}
                    {(document.sizeBytes / 1024).toFixed(1)} KB
                  </span>
                  <code>{document.sha256.slice(0, 12)}…</code>
                </div>
              ))}
            </div>
            {!documents.length ? (
              <Empty>No indexed documents yet. Paths shown here are project-relative only.</Empty>
            ) : null}
          </>
        ) : (
          <Empty>Choose a project before indexing or searching its Binder.</Empty>
        )}
      </Panel>
      <Panel title="Run history" kicker={`${runs.length} RUNS`}>
        {runs.length ? (
          <div className="dashboard-list">
            {runs.map((run) => (
              <button
                className={run.id === selectedRun ? "is-active" : ""}
                type="button"
                key={run.id}
                onClick={() => setSelectedRun(run.id)}
              >
                <strong>{run.kind} · {run.status}</strong>
                <span>{run.objective}</span>
                <Stamp value={run.createdAt} />
              </button>
            ))}
          </div>
        ) : <Empty>No durable runs for this project yet.</Empty>}
      </Panel>
      <Panel title="Event detail" kicker="SAFE METADATA" wide>
        {events.length ? (
          <ol className="event-list">
            {events.map((event) => (
              <li key={event.id}>
                <span>{event.type.replace(/_/g, " ")}</span>
                <strong>{event.summary}</strong>
                <Stamp value={event.createdAt} />
              </li>
            ))}
          </ol>
        ) : <Empty>Select a run to inspect its checkpoints. Arbitrary payloads and worker drafts are intentionally omitted.</Empty>}
      </Panel>
      <Panel title="Tool readiness" kicker={writesEnabled ? "WRITES ON" : "READ-ONLY"}>
        <ul className="status-list">
          {tools.map((tool) => (
            <li key={tool.name}><span>{tool.name}</span><strong>{tool.enabled ? "enabled" : "disabled"}</strong></li>
          ))}
        </ul>
      </Panel>
    </DashboardFrame>
  );
}

function ApprovalsDashboard() {
  const [approvals, setApprovals] = useState<MasterApproval[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [note, setNote] = useState("");
  const refresh = useCallback(async () => {
    setApprovals(await listApprovals(showAll ? "all" : "pending"));
  }, [showAll]);
  const action = useBusyAction(refresh);
  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  return (
    <DashboardFrame
      eyebrow="HUMAN GATE // EXPLICIT AUTHORITY"
      title="Approval Center"
      description="Resolve exact requested targets once. Approval never broadens the requested scope."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Decision queue" kicker={`${approvals.length} RECORDS`} wide>
        <label className="inline-toggle">
          <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.currentTarget.checked)} />
          Include resolved decisions
        </label>
        <label className="compact-label">
          Decision note
          <input value={note} onChange={(event) => setNote(event.currentTarget.value)} placeholder="Why this is safe or should remain blocked" />
        </label>
        {approvals.length ? (
          <div className="approval-list">
            {approvals.map((approval) => (
              <article key={approval.id}>
                <div>
                  <span>{approval.risk.toUpperCase()} RISK · {approval.status}</span>
                  <h3>{approval.actionKind}</h3>
                  <code>{approval.target}</code>
                  <p>{approval.reversible ? "Reversible" : "Not marked reversible"} · {approval.rollbackPlan || "No rollback plan supplied"}</p>
                </div>
                {approval.status === "pending" ? (
                  <div className="decision-actions">
                    <button className="button button--secondary" disabled={action.busy} onClick={() => void action.act(() => resolveApproval(approval.id, "rejected", note || "Rejected in Approval Center."))}>Reject</button>
                    <button className="button button--primary" disabled={action.busy || !note.trim()} onClick={() => void action.act(() => resolveApproval(approval.id, "approved", note.trim()))}>Approve once</button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        ) : <Empty>No {showAll ? "" : "pending "}approval records.</Empty>}
      </Panel>
    </DashboardFrame>
  );
}

function DreamDashboard() {
  const [overview, setOverview] = useState<DreamOverview | null>(null);
  const [projects, setProjects] = useState<MasterProject[]>([]);
  const [recipeId, setRecipeId] = useState("idea-garden");
  const [sourceId, setSourceId] = useState("note-1");
  const [locator, setLocator] = useState("manual-note");
  const [source, setSource] = useState("");
  const [rationale, setRationale] = useState("");
  const [customName, setCustomName] = useState("");
  const [customObjective, setCustomObjective] = useState("");
  const [customProjectId, setCustomProjectId] = useState("");
  const [scheduleId, setScheduleId] = useState("nightly-ideas");
  const [scheduleRecipeId, setScheduleRecipeId] = useState("idea-garden");
  const [scheduleTimezone, setScheduleTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [scheduleTime, setScheduleTime] = useState("02:00");
  const [scheduleEnabled, setScheduleEnabled] = useState(true);
  const [catchUp, setCatchUp] =
    useState<DreamScheduleSummary["catchUp"]>("latest");
  const [graceMinutes, setGraceMinutes] = useState(15);
  const [lookbackDays, setLookbackDays] = useState(7);
  const [maxCatchUp, setMaxCatchUp] = useState(3);
  const [idleMinutes, setIdleMinutes] = useState(5);
  const [maxCpu, setMaxCpu] = useState(60);
  const [minimumMemoryGb, setMinimumMemoryGb] = useState(2);
  const [minimumGpuGb, setMinimumGpuGb] = useState("");
  const [requireNoModelJobs, setRequireNoModelJobs] = useState(true);
  const [requireAcPower, setRequireAcPower] = useState(false);
  const [quietEnabled, setQuietEnabled] = useState(false);
  const [quietStart, setQuietStart] = useState("00:00");
  const [quietEnd, setQuietEnd] = useState("06:00");
  const [scheduleVersion, setScheduleVersion] = useState<number>();
  const refresh = useCallback(async () => {
    const [nextOverview, nextProjects] = await Promise.all([
      getDreamOverview(),
      listProjects(),
    ]);
    setOverview(nextOverview);
    setProjects(nextProjects);
  }, []);
  const action = useBusyAction(refresh);
  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);
  const consentedProjects = projects.filter((project) => project.allowDreaming);
  const manualRecipes =
    overview?.recipes.filter((recipe) => recipe.sourceScopes.length === 0) ?? [];
  const consentedProjectSignature = consentedProjects
    .map((project) => project.id)
    .join("|");
  const recipeSignature =
    overview?.recipes
      .map((recipe) => `${recipe.recipeId}:${recipe.sourceScopes.join(",")}`)
      .join("|") ?? "";
  const selectedScheduleRecipe = overview?.recipes.find(
    (recipe) => recipe.recipeId === scheduleRecipeId,
  );
  function canScheduleRecipe(recipe: DreamRecipeSummary | undefined): boolean {
    return Boolean(
      recipe?.sourceScopes.length &&
        recipe.sourceScopes.every((scope) => {
          if (!scope.startsWith("project:")) return false;
          const projectId = scope.slice("project:".length);
          return consentedProjects.some((project) => project.id === projectId);
        }),
    );
  }
  const selectedRecipeHasConsent = canScheduleRecipe(selectedScheduleRecipe);
  useEffect(() => {
    setCustomProjectId((current) =>
      consentedProjects.some((project) => project.id === current)
        ? current
        : consentedProjects[0]?.id ?? "",
    );
    setRecipeId((current) =>
      manualRecipes.some((recipe) => recipe.recipeId === current)
        ? current
        : manualRecipes[0]?.recipeId ?? "",
    );
    setScheduleRecipeId((current) => {
      if (overview?.recipes.some((recipe) => recipe.recipeId === current)) {
        return current;
      }
      return (
        overview?.recipes.find((recipe) => recipe.sourceScopes.length > 0)
          ?.recipeId ??
        overview?.recipes[0]?.recipeId ??
        ""
      );
    });
  }, [consentedProjectSignature, recipeSignature]);
  useEffect(() => {
    if (scheduleVersion !== undefined) return;
    const current = overview?.recipes.find(
      (recipe) => recipe.recipeId === scheduleRecipeId,
    );
    if (canScheduleRecipe(current)) return;
    const readyRecipe = overview?.recipes.find((recipe) =>
      canScheduleRecipe(recipe),
    );
    if (readyRecipe) {
      setScheduleRecipeId(readyRecipe.recipeId);
      setScheduleEnabled(true);
    } else {
      setScheduleEnabled(false);
    }
  }, [
    consentedProjectSignature,
    recipeSignature,
    scheduleRecipeId,
    scheduleVersion,
  ]);

  function submitDream(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      await runManualDream({ recipeId, sourceId, locator, content: source });
      setSource("");
    });
  }

  function submitRecipe(event: FormEvent) {
    event.preventDefault();
    const id = customName.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-");
    if (!customProjectId) return;
    void action.act(async () => {
      await saveDreamRecipe({
        recipeId: id,
        name: customName,
        objective: customObjective,
        sourceScopes: [`project:${customProjectId}`],
      });
      setCustomName("");
      setCustomObjective("");
      setScheduleRecipeId(id);
      setScheduleEnabled(true);
    });
  }

  function resetScheduleEditor() {
    setScheduleId("nightly-ideas");
    const readyRecipe = overview?.recipes.find(
      (recipe) =>
        recipe.sourceScopes.length > 0 &&
        recipe.sourceScopes.every((scope) => {
          if (!scope.startsWith("project:")) return false;
          const projectId = scope.slice("project:".length);
          return projects.some(
            (project) => project.id === projectId && project.allowDreaming,
          );
        }),
    );
    setScheduleRecipeId(
      readyRecipe?.recipeId ?? overview?.recipes[0]?.recipeId ?? "",
    );
    setScheduleTime("02:00");
    setScheduleEnabled(Boolean(readyRecipe));
    setCatchUp("latest");
    setGraceMinutes(15);
    setLookbackDays(7);
    setMaxCatchUp(3);
    setIdleMinutes(5);
    setMaxCpu(60);
    setMinimumMemoryGb(2);
    setMinimumGpuGb("");
    setRequireNoModelJobs(true);
    setRequireAcPower(false);
    setQuietEnabled(false);
    setQuietStart("00:00");
    setQuietEnd("06:00");
    setScheduleVersion(undefined);
  }

  function editSchedule(schedule: DreamScheduleSummary) {
    setScheduleId(schedule.scheduleId);
    setScheduleRecipeId(schedule.recipeId);
    setScheduleTimezone(schedule.timezone);
    setScheduleTime(schedule.localTime.slice(0, 5));
    setScheduleEnabled(schedule.enabled);
    setCatchUp(schedule.catchUp);
    setGraceMinutes(schedule.onTimeGraceSeconds / 60);
    setLookbackDays(schedule.maxLookbackDays);
    setMaxCatchUp(schedule.maxCatchUpWindows);
    setIdleMinutes(schedule.resourceRules.minIdleSeconds / 60);
    setMaxCpu(schedule.resourceRules.maxCpuPercent);
    setMinimumMemoryGb(
      schedule.resourceRules.minAvailableMemoryBytes / 1024 ** 3,
    );
    setMinimumGpuGb(
      schedule.resourceRules.minGpuFreeBytes === undefined
        ? ""
        : String(schedule.resourceRules.minGpuFreeBytes / 1024 ** 3),
    );
    setRequireNoModelJobs(schedule.resourceRules.requireNoModelJobs);
    setRequireAcPower(schedule.resourceRules.requireAcPower);
    setQuietEnabled(Boolean(schedule.quietWindow));
    setQuietStart(schedule.quietWindow?.startLocal.slice(0, 5) ?? "00:00");
    setQuietEnd(schedule.quietWindow?.endLocal.slice(0, 5) ?? "06:00");
    setScheduleVersion(schedule.version);
  }

  function submitSchedule(event: FormEvent) {
    event.preventDefault();
    if (scheduleEnabled && !selectedRecipeHasConsent) return;
    void action.act(async () => {
      await saveDreamSchedule({
        scheduleId,
        recipeId: scheduleRecipeId,
        timezone: scheduleTimezone,
        localTime: scheduleTime,
        enabled: scheduleEnabled,
        catchUp,
        onTimeGraceSeconds: Math.round(graceMinutes * 60),
        maxLookbackDays: lookbackDays,
        maxCatchUpWindows: maxCatchUp,
        resourceRules: {
          minIdleSeconds: Math.round(idleMinutes * 60),
          maxCpuPercent: maxCpu,
          minAvailableMemoryBytes: Math.round(minimumMemoryGb * 1024 ** 3),
          minGpuFreeBytes: minimumGpuGb
            ? Math.round(Number(minimumGpuGb) * 1024 ** 3)
            : undefined,
          requireNoModelJobs,
          requireAcPower,
        },
        quietWindow: quietEnabled
          ? {
              timezone: scheduleTimezone,
              startLocal: quietStart,
              endLocal: quietEnd,
              weekdays: [0, 1, 2, 3, 4, 5, 6],
            }
          : undefined,
        expectedVersion: scheduleVersion,
      });
      resetScheduleEditor();
    });
  }

  return (
    <DashboardFrame
      eyebrow="ALL-MODEL IDEATION // PROPOSAL ONLY"
      title="Dream Lab"
      description="Run explicit source material through the local council and review every speculative proposal before promotion."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Manual Dream" kicker="EXPLICIT SOURCES">
        <form className="compact-form" onSubmit={submitDream}>
          <label>Manual recipe<select value={recipeId} onChange={(event) => setRecipeId(event.currentTarget.value)}>{manualRecipes.map((recipe) => <option value={recipe.recipeId} key={recipe.recipeId}>{recipe.name}</option>)}</select></label>
          <div className="compact-form__row">
            <label>Source ID<input value={sourceId} onChange={(event) => setSourceId(event.currentTarget.value)} required /></label>
            <label>Locator<input value={locator} onChange={(event) => setLocator(event.currentTarget.value)} required /></label>
          </div>
          <label>Source content<textarea rows={5} value={source} onChange={(event) => setSource(event.currentTarget.value)} required placeholder="Only the material you explicitly place here is included." /></label>
          <button className="button button--primary" disabled={action.busy || !source.trim() || !recipeId}>Run all-model Dream</button>
          <small>
            Unscoped recipes remain available here for one-off explicit notes.
            Proposal-only · no automatic promotion.
          </small>
        </form>
      </Panel>
      <Panel title="Recipe builder" kicker={`${overview?.recipes.length ?? 0} RECIPES`}>
        <form className="compact-form" onSubmit={submitRecipe}>
          <input value={customName} onChange={(event) => setCustomName(event.currentTarget.value)} placeholder="Recipe name" required />
          <textarea value={customObjective} onChange={(event) => setCustomObjective(event.currentTarget.value)} placeholder="Bounded objective" rows={4} required />
          <label>
            Consented Project Binder
            <select
              value={customProjectId}
              onChange={(event) =>
                setCustomProjectId(event.currentTarget.value)
              }
              required
            >
              <option value="">Choose a project with scheduled use enabled</option>
              {consentedProjects.map((project) => (
                <option value={project.id} key={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <small>
            This stores one explicit scope:{" "}
            {customProjectId ? `project:${customProjectId}` : "none selected"}.
            Enable scheduled Dream consent from that project's Binder first.
          </small>
          <button
            className="button button--secondary"
            disabled={action.busy || !customProjectId}
          >
            Save scoped recipe
          </button>
        </form>
        <div className="recipe-scope-list">
          {overview?.recipes.map((recipe) => (
            <article key={recipe.recipeId}>
              <div>
                <strong>{recipe.name}</strong>
                <span>{recipe.objective}</span>
              </div>
              <code>
                {recipe.sourceScopes.length
                  ? recipe.sourceScopes.join(", ")
                  : "MANUAL ONLY · NO SOURCE SCOPE"}
              </code>
            </article>
          ))}
        </div>
        {!consentedProjects.length ? (
          <Empty>
            No project has granted future scheduled Binder use. Open Projects
            and enable it explicitly before creating a scheduled recipe.
          </Empty>
        ) : null}
      </Panel>
      <Panel
        title="Dream schedule"
        kicker={
          overview?.scheduledExecutionEnabled
            ? "SCHEDULER RUNNING"
            : overview?.backgroundConfigured
              ? "SCHEDULER PAUSED"
              : "SCHEDULER UNAVAILABLE"
        }
        wide
      >
        <form className="compact-form" onSubmit={submitSchedule}>
          <div className="compact-form__row">
            <label>
              Schedule ID
              <input
                value={scheduleId}
                onChange={(event) => setScheduleId(event.currentTarget.value)}
                pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
                readOnly={scheduleVersion !== undefined}
                required
              />
            </label>
            <label>
              Recipe
              <select
                value={scheduleRecipeId}
                onChange={(event) => {
                  const nextId = event.currentTarget.value;
                  setScheduleRecipeId(nextId);
                  const nextRecipe = overview?.recipes.find(
                    (recipe) => recipe.recipeId === nextId,
                  );
                  if (!canScheduleRecipe(nextRecipe)) {
                    setScheduleEnabled(false);
                  }
                }}
                required
              >
                {overview?.recipes.map((recipe) => (
                  <option
                    value={recipe.recipeId}
                    key={recipe.recipeId}
                    disabled={
                      scheduleVersion === undefined &&
                      !canScheduleRecipe(recipe)
                    }
                  >
                    {recipe.name} —{" "}
                    {canScheduleRecipe(recipe)
                      ? recipe.sourceScopes.join(", ")
                      : recipe.sourceScopes.length
                        ? "consent revoked"
                        : "manual only"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Local time
              <input
                type="time"
                value={scheduleTime}
                onChange={(event) => setScheduleTime(event.currentTarget.value)}
                required
              />
            </label>
          </div>
          <div className="compact-form__row">
            <label>
              IANA timezone
              <input
                value={scheduleTimezone}
                onChange={(event) =>
                  setScheduleTimezone(event.currentTarget.value)
                }
                required
              />
            </label>
            <label>
              Catch-up
              <select
                value={catchUp}
                onChange={(event) =>
                  setCatchUp(
                    event.currentTarget.value as DreamScheduleSummary["catchUp"],
                  )
                }
              >
                <option value="skip">Skip missed</option>
                <option value="latest">Latest missed only</option>
                <option value="all_bounded">All, bounded</option>
              </select>
            </label>
            <label>
              Grace (minutes)
              <input
                type="number"
                min={0}
                max={1440}
                value={graceMinutes}
                onChange={(event) =>
                  setGraceMinutes(event.currentTarget.valueAsNumber)
                }
              />
            </label>
          </div>
          <details className="advanced-controls">
            <summary>Resource and catch-up limits</summary>
            <div className="compact-form__row">
              <label>
                Idle minutes
                <input
                  type="number"
                  min={0}
                  value={idleMinutes}
                  onChange={(event) =>
                    setIdleMinutes(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <label>
                Max CPU %
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={maxCpu}
                  onChange={(event) =>
                    setMaxCpu(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <label>
                Free memory GB
                <input
                  type="number"
                  min={0}
                  step="0.25"
                  value={minimumMemoryGb}
                  onChange={(event) =>
                    setMinimumMemoryGb(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <label>
                Free GPU GB (optional)
                <input
                  type="number"
                  min={0}
                  step="0.25"
                  value={minimumGpuGb}
                  onChange={(event) =>
                    setMinimumGpuGb(event.currentTarget.value)
                  }
                />
              </label>
            </div>
            <div className="compact-form__row">
              <label>
                Lookback days
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={lookbackDays}
                  onChange={(event) =>
                    setLookbackDays(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <label>
                Max catch-up runs
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={maxCatchUp}
                  onChange={(event) =>
                    setMaxCatchUp(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
            </div>
            <label className="inline-toggle">
              <input
                type="checkbox"
                checked={requireNoModelJobs}
                onChange={(event) =>
                  setRequireNoModelJobs(event.currentTarget.checked)
                }
              />
              Wait until foreground model work is idle
            </label>
            <label className="inline-toggle">
              <input
                type="checkbox"
                checked={requireAcPower}
                onChange={(event) =>
                  setRequireAcPower(event.currentTarget.checked)
                }
              />
              Require AC power
            </label>
            <label className="inline-toggle">
              <input
                type="checkbox"
                checked={quietEnabled}
                onChange={(event) =>
                  setQuietEnabled(event.currentTarget.checked)
                }
              />
              Restrict starts to a quiet window
            </label>
            {quietEnabled ? (
              <div className="compact-form__row">
                <label>
                  Window starts
                  <input
                    type="time"
                    value={quietStart}
                    onChange={(event) =>
                      setQuietStart(event.currentTarget.value)
                    }
                  />
                </label>
                <label>
                  Window ends
                  <input
                    type="time"
                    value={quietEnd}
                    onChange={(event) =>
                      setQuietEnd(event.currentTarget.value)
                    }
                  />
                </label>
              </div>
            ) : null}
          </details>
          <label className="inline-toggle">
            <input
              type="checkbox"
              checked={scheduleEnabled}
              disabled={!selectedRecipeHasConsent && !scheduleEnabled}
              onChange={(event) => {
                if (event.currentTarget.checked && !selectedRecipeHasConsent) {
                  return;
                }
                setScheduleEnabled(event.currentTarget.checked);
              }}
            />
            Enable after saving
          </label>
          {!selectedRecipeHasConsent ? (
            <small className="schedule-consent-warning">
              {selectedScheduleRecipe?.sourceScopes.length
                ? "This recipe's project consent is no longer active. Existing schedules can be paused, edited while disabled, or deleted."
                : "This unscoped recipe is manual-only. Choose a scoped recipe tied to a consented Project Binder before enabling a schedule."}
            </small>
          ) : (
            <small>
              Scheduled source:{" "}
              {selectedScheduleRecipe?.sourceScopes.join(", ")}
            </small>
          )}
          <div className="decision-actions">
            <button
              className="button button--primary"
              disabled={
                action.busy ||
                (scheduleEnabled && !selectedRecipeHasConsent)
              }
            >
              {scheduleVersion === undefined ? "Create schedule" : "Save changes"}
            </button>
            {scheduleVersion !== undefined ? (
              <button
                className="button button--secondary"
                type="button"
                onClick={resetScheduleEditor}
              >
                Cancel edit
              </button>
            ) : null}
          </div>
        </form>
        <div className="schedule-list">
          {overview?.schedules.map((schedule) => (
            <article key={schedule.scheduleId}>
              <div>
                <strong>{schedule.scheduleId}</strong>
                <span>
                  {schedule.recipeId} · {schedule.localTime.slice(0, 5)}{" "}
                  {schedule.timezone} ·{" "}
                  {schedule.catchUp.replace("_", " ")} · v{schedule.version}
                </span>
              </div>
              <div className="decision-actions">
                <button
                  type="button"
                  disabled={action.busy}
                  onClick={() => editSchedule(schedule)}
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={
                    action.busy ||
                    (!schedule.enabled &&
                      !canScheduleRecipe(
                        overview.recipes.find(
                          (recipe) => recipe.recipeId === schedule.recipeId,
                        ),
                      ))
                  }
                  title={
                    !schedule.enabled &&
                    !canScheduleRecipe(
                      overview.recipes.find(
                        (recipe) => recipe.recipeId === schedule.recipeId,
                      ),
                    )
                      ? "This recipe is unscoped or its project consent was revoked."
                      : undefined
                  }
                  onClick={() =>
                    void action.act(() =>
                      setDreamScheduleEnabled(
                        schedule.scheduleId,
                        !schedule.enabled,
                      ),
                    )
                  }
                >
                  {schedule.enabled ? "Pause" : "Enable"}
                </button>
                <button
                  type="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action.act(() =>
                      deleteDreamSchedule(schedule.scheduleId),
                    )
                  }
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
          {!overview?.schedules.length ? (
            <Empty>No app-owned Dream schedules yet.</Empty>
          ) : null}
        </div>
      </Panel>
      <Panel title="Proposal Inbox" kicker={`${overview?.inbox.filter((item) => item.disposition === "pending").length ?? 0} PENDING`} wide>
        <label className="compact-label">Review rationale<input value={rationale} onChange={(event) => setRationale(event.currentTarget.value)} placeholder="Required to promote; recorded with the decision" /></label>
        {overview?.inbox.length ? (
          <div className="proposal-list">
            {overview.inbox.map((item) => (
              <article key={item.itemId}>
                <span>{item.epistemicLabel.toUpperCase()} · {item.disposition}</span>
                <p>{item.proposalText}</p>
                <small>Sources: {item.sourceRefs.join(", ")}</small>
                {item.disposition === "pending" ? (
                  <div className="decision-actions">
                    <button className="button button--secondary" disabled={action.busy} onClick={() => void action.act(() => decideDreamItem(item.itemId, "reject", rationale || "Rejected during review."))}>Reject</button>
                    <button className="button button--primary" disabled={action.busy || !rationale.trim()} onClick={() => void action.act(() => decideDreamItem(item.itemId, "promote", rationale.trim()))}>Promote candidate</button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        ) : <Empty>No Dream proposals yet.</Empty>}
      </Panel>
      <Panel title="Run ledger" kicker={`${overview?.runs.length ?? 0} RUNS`}>
        <ul className="status-list">{overview?.runs.map((run) => <li key={run.runId}><span>{run.recipeId}</span><strong>{run.status}</strong></li>)}</ul>
      </Panel>
    </DashboardFrame>
  );
}

function safeArtifactDownloadName(
  artifact: ComfyArtifactSummary,
): string {
  const pathParts = artifact.originalFilename.split(/[/\\]/).filter(Boolean);
  const basename = pathParts[pathParts.length - 1] ?? "";
  const sanitized = basename
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .slice(0, 180);
  return sanitized || `${artifact.id}.bin`;
}

function ComfyArtifactCard({
  jobId,
  artifact,
}: {
  jobId: string;
  artifact: ComfyArtifactSummary;
}) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const previewKind = artifact.mediaType.startsWith("image/")
    ? "image"
    : artifact.mediaType.startsWith("audio/")
      ? "audio"
      : artifact.mediaType.startsWith("video/")
        ? "video"
        : undefined;

  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );

  async function loadArtifact() {
    setLoading(true);
    setError("");
    try {
      const blob = await getComfyArtifactContent(jobId, artifact.id);
      if (blob.size !== artifact.sizeBytes) {
        throw new Error(
          "Downloaded artifact size did not match its verified manifest.",
        );
      }
      setUrl(URL.createObjectURL(blob));
    } catch (caught) {
      setError(formatProjectMasterError(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <article className="comfy-artifact-card">
      <header>
        <div>
          <strong>{artifact.originalFilename}</strong>
          <span>
            {artifact.mediaType} · {(artifact.sizeBytes / 1024).toFixed(1)} KB
          </span>
        </div>
        <b className={artifact.verified ? "is-verified" : ""}>
          {artifact.verified ? "VERIFIED" : "UNVERIFIED"}
        </b>
      </header>
      {url && previewKind === "image" ? (
        <img
          className="comfy-artifact-preview"
          src={url}
          alt={`ComfyUI output ${artifact.originalFilename}`}
          loading="lazy"
        />
      ) : null}
      {url && previewKind === "audio" ? (
        <audio className="comfy-artifact-preview" src={url} controls />
      ) : null}
      {url && previewKind === "video" ? (
        <video
          className="comfy-artifact-preview"
          src={url}
          controls
          preload="metadata"
        />
      ) : null}
      <dl>
        <div>
          <dt>SHA-256</dt>
          <dd title={artifact.sha256}>{artifact.sha256}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            node {artifact.provenance.nodeId} ·{" "}
            {artifact.provenance.category} #{artifact.provenance.outputIndex}
          </dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd title={artifact.provenance.workflowDigest}>
            {artifact.provenance.workflowRevisionId} ·{" "}
            {artifact.provenance.workflowDigest.slice(0, 12)}…
          </dd>
        </div>
        <div>
          <dt>Prompt</dt>
          <dd>{artifact.provenance.remotePromptId}</dd>
        </div>
        <div>
          <dt>Imported</dt>
          <dd>
            <Stamp value={artifact.provenance.fetchedAt || artifact.createdAt} />
          </dd>
        </div>
      </dl>
      <div className="decision-actions">
        {!url ? (
          <button
            className="button button--secondary"
            type="button"
            disabled={loading}
            onClick={() => void loadArtifact()}
          >
            {loading
              ? "Loading…"
              : previewKind
                ? "Load preview"
                : "Prepare download"}
          </button>
        ) : (
          <>
            <a
              className="button button--secondary"
              href={url}
              download={safeArtifactDownloadName(artifact)}
            >
              Download
            </a>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => setUrl("")}
            >
              Release
            </button>
          </>
        )}
      </div>
      {error ? (
        <small className="artifact-error" role="alert">
          {error}
        </small>
      ) : null}
    </article>
  );
}

function ComfyDashboard() {
  const [overview, setOverview] = useState<ComfyOverview | null>(null);
  const [profileId, setProfileId] = useState("local");
  const [profileName, setProfileName] = useState("Local ComfyUI");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");
  const [trustedHosts, setTrustedHosts] = useState("");
  const [connection, setConnection] = useState<string>();
  const [workflowName, setWorkflowName] = useState("");
  const [workflowJson, setWorkflowJson] = useState("");
  const [workflowBindings, setWorkflowBindings] = useState<
    ComfyWorkflowBinding[]
  >([]);
  const [decisionNote, setDecisionNote] = useState("");
  const [selectedProfile, setSelectedProfile] = useState("");
  const [selectedWorkflow, setSelectedWorkflow] = useState("");
  const [jobValues, setJobValues] = useState<Record<string, unknown>>({});
  const refresh = useCallback(async () => setOverview(await getComfyOverview()), []);
  const action = useBusyAction(refresh);
  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);
  useEffect(() => {
    const profiles = overview?.profiles ?? [];
    const approvedWorkflows =
      overview?.workflows.filter((item) => item.trustState === "approved") ?? [];
    setSelectedProfile((current) =>
      profiles.some((item) => item.id === current)
        ? current
        : profiles[0]?.id ?? "",
    );
    setSelectedWorkflow((current) =>
      approvedWorkflows.some((item) => item.id === current)
        ? current
        : approvedWorkflows[0]?.id ?? "",
    );
  }, [overview]);
  const selectedWorkflowSummary = overview?.workflows.find(
    (item) => item.id === selectedWorkflow,
  );
  const bindingSignature = selectedWorkflowSummary?.bindings
    .map((binding) => `${binding.id}:${String(binding.defaultValue)}`)
    .join("|");
  useEffect(() => {
    const workflow = overview?.workflows.find(
      (item) => item.id === selectedWorkflow,
    );
    if (!workflow) {
      setJobValues({});
      return;
    }
    setJobValues(
      Object.fromEntries(
        workflow.bindings.map((binding) => [
          binding.id,
          binding.defaultValue ??
            (binding.valueType === "boolean" ? false : ""),
        ]),
      ),
    );
    // The signature changes only when the immutable binding contract changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindingSignature, selectedWorkflow]);

  function submitProfile(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      await saveComfyProfile({
        id: profileId,
        name: profileName,
        baseUrl,
        trustedHosts: trustedHosts
          .split(",")
          .map((host) => host.trim())
          .filter(Boolean),
      });
      setSelectedProfile(profileId);
    });
  }

  function submitWorkflow(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      const parsed = JSON.parse(workflowJson) as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("ComfyUI API workflow must be a JSON object.");
      }
      await importComfyWorkflow(
        workflowName,
        parsed as Record<string, unknown>,
        workflowBindings,
      );
      setWorkflowName("");
      setWorkflowJson("");
      setWorkflowBindings([]);
    });
  }

  function addBinding(
    id: "prompt" | "seed" | "width" | "height" | "custom",
  ) {
    const base =
      id === "prompt"
        ? {
            id: "prompt",
            inputName: "text",
            valueType: "string" as const,
            description: "Positive prompt",
          }
        : id === "seed"
          ? {
              id: "seed",
              inputName: "seed",
              valueType: "integer" as const,
              description: "Generation seed",
            }
          : id === "width" || id === "height"
            ? {
                id,
                inputName: id,
                valueType: "integer" as const,
                description: `${id[0].toUpperCase()}${id.slice(1)} in pixels`,
              }
            : {
                id: `value-${workflowBindings.length + 1}`,
                inputName: "",
                valueType: "string" as const,
                description: "",
              };
    setWorkflowBindings((current) => [
      ...current,
      {
        ...base,
        nodeId: "",
        required: true,
        defaultValue:
          id === "seed" ? 0 : id === "width" || id === "height" ? 1024 : undefined,
        minimum: id === "width" || id === "height" ? 64 : undefined,
        maximum: id === "width" || id === "height" ? 8192 : undefined,
        choices: [],
      },
    ]);
  }

  function updateBinding(
    index: number,
    update: Partial<ComfyWorkflowBinding>,
  ) {
    setWorkflowBindings((current) =>
      current.map((binding, itemIndex) =>
        itemIndex === index ? { ...binding, ...update } : binding,
      ),
    );
  }

  function coerceJobValue(
    binding: ComfyWorkflowBinding,
    raw: string | boolean,
  ): unknown {
    if (binding.valueType === "boolean") return Boolean(raw);
    if (binding.valueType === "enum") {
      return binding.choices.find((choice) => String(choice) === String(raw));
    }
    if (binding.valueType === "integer") return Number.parseInt(String(raw), 10);
    if (binding.valueType === "number") return Number.parseFloat(String(raw));
    return raw;
  }

  const jobValuesValid = Boolean(
    selectedWorkflowSummary?.bindings.every((binding) => {
      const value = jobValues[binding.id];
      if (binding.required && (value === "" || value === undefined)) return false;
      if (
        ["integer", "number"].includes(binding.valueType) &&
        typeof value === "number"
      ) {
        return (
          Number.isFinite(value) &&
          (binding.minimum === undefined || value >= binding.minimum) &&
          (binding.maximum === undefined || value <= binding.maximum)
        );
      }
      return true;
    }) ?? false,
  );

  return (
    <DashboardFrame
      eyebrow="MEDIA WORKSPACE // COMFYUI"
      title="Creator"
      description="Connect a trusted ComfyUI endpoint, review immutable API workflows, and control Project Master-owned jobs."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Connection profile" kicker={overview?.profiles.length ? "CONFIGURED" : "OFFLINE DEFAULT"}>
        <form className="compact-form" onSubmit={submitProfile}>
          <div className="compact-form__row">
            <label>ID<input value={profileId} onChange={(event) => setProfileId(event.currentTarget.value)} required /></label>
            <label>Name<input value={profileName} onChange={(event) => setProfileName(event.currentTarget.value)} required /></label>
          </div>
          <label>Base URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.currentTarget.value)} required /></label>
          <label>
            Trusted remote hosts (optional)
            <input
              value={trustedHosts}
              onChange={(event) => setTrustedHosts(event.currentTarget.value)}
              placeholder="comfy.example.net, 192.168.1.25"
            />
          </label>
          <small>
            Loopback needs no entry. Every remote hostname must be listed
            explicitly, uses verified HTTPS, and is stored with this profile.
            No secret material is accepted here.
          </small>
          <div className="decision-actions">
            <button className="button button--secondary" disabled={action.busy}>Save</button>
            <button className="button button--secondary" type="button" disabled={!selectedProfile || action.busy} onClick={() => void action.act(async () => {
              const result = await getComfyProfileStatus(selectedProfile);
              setConnection(result.ok ? "Connected" : result.error || "Offline");
            })}>Test selected</button>
          </div>
          {connection ? <span className="form-status">{connection}</span> : null}
        </form>
      </Panel>
      <Panel title="Import API workflow" kicker="IMMUTABLE REVISION">
        <form className="compact-form" onSubmit={submitWorkflow}>
          <input value={workflowName} onChange={(event) => setWorkflowName(event.currentTarget.value)} placeholder="Workflow name" required />
          <textarea value={workflowJson} onChange={(event) => setWorkflowJson(event.currentTarget.value)} rows={7} placeholder='Paste ComfyUI "Save (API Format)" JSON' required />
          <div className="binding-toolbar">
            <span>Safe job inputs</span>
            {(["prompt", "seed", "width", "height", "custom"] as const).map(
              (preset) => (
                <button
                  className="button button--secondary"
                  type="button"
                  key={preset}
                  onClick={() => addBinding(preset)}
                >
                  + {preset}
                </button>
              ),
            )}
          </div>
          {workflowBindings.map((binding, index) => (
            <fieldset className="binding-editor" key={`${binding.id}-${index}`}>
              <legend>Binding {index + 1}</legend>
              <div className="compact-form__row">
                <label>
                  Public ID
                  <input
                    value={binding.id}
                    pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
                    onChange={(event) =>
                      updateBinding(index, { id: event.currentTarget.value })
                    }
                    required
                  />
                </label>
                <label>
                  Node ID
                  <input
                    value={binding.nodeId}
                    onChange={(event) =>
                      updateBinding(index, {
                        nodeId: event.currentTarget.value,
                      })
                    }
                    placeholder="e.g. 6"
                    required
                  />
                </label>
                <label>
                  Node input
                  <input
                    value={binding.inputName}
                    onChange={(event) =>
                      updateBinding(index, {
                        inputName: event.currentTarget.value,
                      })
                    }
                    placeholder="e.g. text, seed, width"
                    required
                  />
                </label>
                <label>
                  Type
                  <select
                    value={binding.valueType}
                    onChange={(event) =>
                      updateBinding(index, {
                        valueType: event.currentTarget
                          .value as ComfyWorkflowBinding["valueType"],
                      })
                    }
                  >
                    <option value="string">Text</option>
                    <option value="integer">Integer</option>
                    <option value="number">Number</option>
                    <option value="boolean">On/off</option>
                    <option value="enum">Choice</option>
                  </select>
                </label>
              </div>
              <div className="compact-form__row">
                <label>
                  Default
                  <input
                    value={
                      typeof binding.defaultValue === "boolean"
                        ? String(binding.defaultValue)
                        : String(binding.defaultValue ?? "")
                    }
                    onChange={(event) => {
                      const raw = event.currentTarget.value;
                      updateBinding(index, {
                        defaultValue:
                          raw === ""
                            ? undefined
                            : binding.valueType === "integer"
                              ? Number.parseInt(raw, 10)
                              : binding.valueType === "number"
                                ? Number.parseFloat(raw)
                                : binding.valueType === "boolean"
                                  ? raw === "true"
                                  : raw,
                      });
                    }}
                  />
                </label>
                <label>
                  Minimum
                  <input
                    type="number"
                    disabled={
                      !["integer", "number"].includes(binding.valueType)
                    }
                    value={binding.minimum ?? ""}
                    onChange={(event) =>
                      updateBinding(index, {
                        minimum:
                          event.currentTarget.value === ""
                            ? undefined
                            : event.currentTarget.valueAsNumber,
                      })
                    }
                  />
                </label>
                <label>
                  Maximum
                  <input
                    type="number"
                    disabled={
                      !["integer", "number"].includes(binding.valueType)
                    }
                    value={binding.maximum ?? ""}
                    onChange={(event) =>
                      updateBinding(index, {
                        maximum:
                          event.currentTarget.value === ""
                            ? undefined
                            : event.currentTarget.valueAsNumber,
                      })
                    }
                  />
                </label>
                <label>
                  Choices
                  <input
                    disabled={binding.valueType !== "enum"}
                    value={binding.choices.join(", ")}
                    onChange={(event) =>
                      updateBinding(index, {
                        choices: event.currentTarget.value
                          .split(",")
                          .map((item) => item.trim())
                          .filter(Boolean),
                      })
                    }
                    placeholder="one, two, three"
                  />
                </label>
              </div>
              <label>
                Description
                <input
                  value={binding.description}
                  onChange={(event) =>
                    updateBinding(index, {
                      description: event.currentTarget.value,
                    })
                  }
                />
              </label>
              <div className="decision-actions">
                <label className="inline-toggle">
                  <input
                    type="checkbox"
                    checked={binding.required}
                    onChange={(event) =>
                      updateBinding(index, {
                        required: event.currentTarget.checked,
                      })
                    }
                  />
                  Required per job
                </label>
                <button
                  type="button"
                  onClick={() =>
                    setWorkflowBindings((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                >
                  Remove
                </button>
              </div>
            </fieldset>
          ))}
          <small>
            Bindings can change only declared node inputs. The backend validates
            node IDs, types, ranges, and immutable workflow digest before use.
          </small>
          <button className="button button--secondary" disabled={action.busy}>Validate and import</button>
        </form>
      </Panel>
      <Panel title="Workflow trust" kicker={`${overview?.workflows.length ?? 0} REVISIONS`} wide>
        <label className="compact-label">Decision note<input value={decisionNote} onChange={(event) => setDecisionNote(event.currentTarget.value)} placeholder="Review finding" /></label>
        <div className="workflow-list">
          {overview?.workflows.map((workflow) => (
            <article key={workflow.id}>
              <div><span>{workflow.trustState.toUpperCase()}</span><strong>{workflow.name}</strong><code>{workflow.digest.slice(0, 16)}</code></div>
              <div className="decision-actions">
                <button className="button button--secondary" disabled={action.busy} onClick={() => void action.act(() => decideComfyWorkflow(workflow.id, "rejected", decisionNote || "Rejected during review."))}>Reject</button>
                <button className="button button--primary" disabled={action.busy || !decisionNote.trim()} onClick={() => void action.act(() => decideComfyWorkflow(workflow.id, "approved", decisionNote.trim()))}>Approve revision</button>
              </div>
            </article>
          ))}
          {!overview?.workflows.length ? <Empty>No workflow revisions imported.</Empty> : null}
        </div>
      </Panel>
      <Panel title="Queue job" kicker="APPROVED ONLY">
        <div className="compact-form">
          <label>Profile<select value={selectedProfile} onChange={(event) => setSelectedProfile(event.currentTarget.value)}>{overview?.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          <label>Workflow<select value={selectedWorkflow} onChange={(event) => setSelectedWorkflow(event.currentTarget.value)}>{overview?.workflows.filter((item) => item.trustState === "approved").map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}</select></label>
          {selectedWorkflowSummary?.bindings.map((binding) => (
            <label key={binding.id}>
              {binding.description || binding.id}
              {binding.valueType === "boolean" ? (
                <input
                  type="checkbox"
                  checked={Boolean(jobValues[binding.id])}
                  onChange={(event) =>
                    setJobValues((current) => ({
                      ...current,
                      [binding.id]: event.currentTarget.checked,
                    }))
                  }
                />
              ) : binding.valueType === "enum" ? (
                <select
                  value={String(jobValues[binding.id] ?? "")}
                  onChange={(event) =>
                    setJobValues((current) => ({
                      ...current,
                      [binding.id]: coerceJobValue(
                        binding,
                        event.currentTarget.value,
                      ),
                    }))
                  }
                  required={binding.required}
                >
                  <option value="">Choose…</option>
                  {binding.choices.map((choice) => (
                    <option value={String(choice)} key={String(choice)}>
                      {String(choice)}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={
                    ["integer", "number"].includes(binding.valueType)
                      ? "number"
                      : "text"
                  }
                  step={binding.valueType === "integer" ? 1 : undefined}
                  min={binding.minimum}
                  max={binding.maximum}
                  value={String(jobValues[binding.id] ?? "")}
                  onChange={(event) =>
                    setJobValues((current) => ({
                      ...current,
                      [binding.id]: coerceJobValue(
                        binding,
                        event.currentTarget.value,
                      ),
                    }))
                  }
                  required={binding.required}
                />
              )}
              <small>
                {binding.id} → node {binding.nodeId}.{binding.inputName}
              </small>
            </label>
          ))}
          {!selectedWorkflowSummary?.bindings.length ? (
            <small>
              This revision exposes no per-job values and will run exactly as
              imported.
            </small>
          ) : null}
          <button
            className="button button--primary"
            disabled={
              !selectedProfile ||
              !selectedWorkflow ||
              !jobValuesValid ||
              action.busy
            }
            onClick={() =>
              void action.act(() =>
                createComfyJob({
                  profileId: selectedProfile,
                  workflowRevisionId: selectedWorkflow,
                  values: jobValues,
                }),
              )
            }
          >
            Run workflow
          </button>
        </div>
      </Panel>
      <Panel
        title="Job ledger"
        kicker={`${overview?.jobs.length ?? 0} JOBS`}
        wide
      >
        <ul className="job-list comfy-job-list">
          {overview?.jobs.map((job) => (
            <li key={job.id}>
              <div className="comfy-job-header">
                <div>
                  <strong>{job.status}</strong>
                  <span>{job.workflowRevisionId}</span>
                  <code>{job.id}</code>
                </div>
                <div className="comfy-artifact-status">
                  <b>{job.artifactStatus.toUpperCase()}</b>
                  <span>
                    {job.artifacts.length} persisted artifact
                    {job.artifacts.length === 1 ? "" : "s"}
                  </span>
                </div>
                {!["succeeded", "failed", "cancelled"].includes(job.status) ? (
                  <button
                    disabled={action.busy}
                    onClick={() =>
                      void action.act(() => cancelComfyJob(job.id))
                    }
                  >
                    Cancel
                  </button>
                ) : null}
              </div>
              {job.error ? (
                <small className="artifact-error">{job.error}</small>
              ) : null}
              {job.artifactError ? (
                <small className="artifact-error">
                  Artifact import: {job.artifactError}
                </small>
              ) : null}
              {job.artifacts.length ? (
                <div className="comfy-artifact-gallery">
                  {job.artifacts.map((artifact) => (
                    <ComfyArtifactCard
                      jobId={job.id}
                      artifact={artifact}
                      key={artifact.id}
                    />
                  ))}
                </div>
              ) : job.status === "succeeded" ? (
                <small>
                  No local artifact is available. Status: {job.artifactStatus}.
                </small>
              ) : null}
            </li>
          ))}
        </ul>
        {!overview?.jobs.length ? <Empty>No ComfyUI jobs. Offline is expected until a profile is reachable.</Empty> : null}
      </Panel>
    </DashboardFrame>
  );
}

function ArtifactPlayer({ artifactId }: { artifactId: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  async function load() {
    try {
      const next = URL.createObjectURL(await getVoiceArtifactContent(artifactId));
      if (url) URL.revokeObjectURL(url);
      setUrl(next);
    } catch (caught) {
      setError(formatProjectMasterError(caught));
    }
  }
  return (
    <div className="artifact-player">
      {url ? <audio controls src={url} /> : <button className="button button--secondary" type="button" onClick={() => void load()}>Load audio</button>}
      {url ? <a className="button button--secondary" href={url} download={`${artifactId}.audio`}>Download</a> : null}
      {error ? <small>{error}</small> : null}
    </div>
  );
}

function VoiceDashboard() {
  const [overview, setOverview] = useState<VoiceOverview | null>(null);
  const [engineHealth, setEngineHealth] = useState<
    Record<string, VoiceEngineHealth>
  >({});
  const [runningJobs, setRunningJobs] = useState<Set<string>>(() => new Set());
  const [cancellingJobs, setCancellingJobs] = useState<Set<string>>(
    () => new Set(),
  );
  const [profileId, setProfileId] = useState("studio-voice");
  const [profileName, setProfileName] = useState("Studio Voice");
  const [language, setLanguage] = useState("en-US");
  const [description, setDescription] = useState("");
  const [publication, setPublication] = useState(false);
  const [commercial, setCommercial] = useState(false);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referenceTranscript, setReferenceTranscript] = useState("");
  const [referenceArtifactId, setReferenceArtifactId] = useState("");
  const [referenceProfileId, setReferenceProfileId] = useState("my-reference-voice");
  const [referenceProfileName, setReferenceProfileName] = useState("My Reference Voice");
  const [rightsBasis, setRightsBasis] = useState<
    | "self_voice"
    | "explicit_consent"
    | "licensed_voice"
    | "synthetic_reference"
  >("self_voice");
  const [subjectLabel, setSubjectLabel] = useState("My voice");
  const [rightsNotes, setRightsNotes] = useState("");
  const [projectId, setProjectId] = useState("voice-project");
  const [projectName, setProjectName] = useState("Voice Project");
  const [selectedProfile, setSelectedProfile] = useState("");
  const [script, setScript] = useState("");
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedPack, setSelectedPack] = useState("");
  const [purpose, setPurpose] = useState<"private" | "publication" | "commercial">("private");
  const refresh = useCallback(async () => {
    const next = await getVoiceOverview();
    setOverview(next);
    const results = await Promise.all(
      next.packs
        .filter((pack) => pack.installed)
        .map(async (pack) => {
          try {
            return [pack.id, await getVoiceEngineHealth(pack.id)] as const;
          } catch (caught) {
            return [
              pack.id,
              {
                available: false,
                status: "error",
                detail: formatProjectMasterError(caught),
              } satisfies VoiceEngineHealth,
            ] as const;
          }
        }),
    );
    setEngineHealth(Object.fromEntries(results));
  }, []);
  const pollJobs = useCallback(async () => {
    setOverview(await getVoiceOverview());
  }, []);
  const action = useBusyAction(refresh);
  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);
  useEffect(() => {
    setSelectedProfile((current) => current || overview?.profiles[0]?.id || "");
    setReferenceArtifactId(
      (current) => current || overview?.references[0]?.artifactId || "",
    );
    setSelectedProject((current) => current || overview?.projects[0]?.id || "");
    setSelectedPack((current) => current || overview?.packs.find((pack) => pack.installed)?.id || "");
  }, [overview]);
  const activeJobSignature = overview?.jobs
    .filter((job) => !["succeeded", "failed", "cancelled"].includes(job.status))
    .map((job) => `${job.id}:${job.status}`)
    .join("|");
  useEffect(() => {
    if (!activeJobSignature && runningJobs.size === 0) return;
    const timer = globalThis.setInterval(() => {
      void pollJobs().catch((caught) =>
        action.setError(formatProjectMasterError(caught)),
      );
    }, 2_000);
    return () => globalThis.clearInterval(timer);
  }, [action.setError, activeJobSignature, pollJobs, runningJobs.size]);

  function submitProfile(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      await saveDesignedVoiceProfile({ profileId, name: profileName, language, description, publication });
      setSelectedProfile(profileId);
    });
  }
  function submitProject(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      await saveVoiceProject({ projectId, name: projectName, language, profileId: selectedProfile, script });
      setSelectedProject(projectId);
    });
  }

  function submitReference(event: FormEvent) {
    event.preventDefault();
    if (!referenceFile) return;
    void action.act(async () => {
      const imported = await importVoiceReference(
        referenceFile,
        referenceTranscript,
      );
      setReferenceArtifactId(imported.artifactId);
      setReferenceFile(null);
      setReferenceTranscript("");
    });
  }

  function submitReferenceProfile(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      await saveReferenceVoiceProfile({
        profileId: referenceProfileId,
        name: referenceProfileName,
        language,
        description:
          rightsBasis === "synthetic_reference"
            ? "User-attested synthetic/generated WAV reference."
            : "Rights-attested local WAV reference voice.",
        referenceArtifactId,
        rightsBasis,
        subjectLabel,
        publication,
        commercial,
        notes: rightsNotes,
      });
      setSelectedProfile(referenceProfileId);
    });
  }

  function startVoiceRender(jobId: string) {
    setRunningJobs((current) => new Set(current).add(jobId));
    action.setError(null);
    void runVoiceJob(jobId)
      .catch((caught) => action.setError(formatProjectMasterError(caught)))
      .finally(() => {
        setRunningJobs((current) => {
          const next = new Set(current);
          next.delete(jobId);
          return next;
        });
        void refresh().catch((caught) =>
          action.setError(formatProjectMasterError(caught)),
        );
      });
  }

  async function stopVoiceRender(jobId: string) {
    setCancellingJobs((current) => new Set(current).add(jobId));
    action.setError(null);
    try {
      await cancelVoiceJob(jobId);
      await refresh();
    } catch (caught) {
      action.setError(formatProjectMasterError(caught));
    } finally {
      setCancellingJobs((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
    }
  }

  const selectedPackHealth = selectedPack
    ? engineHealth[selectedPack]
    : undefined;

  return (
    <DashboardFrame
      eyebrow="LOCAL AUDIO // RIGHTS-AWARE"
      title="Voice Studio"
      description="Design synthetic voices, author scripts, render with verified local engine packs, and inspect checksum-verified audio."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Engine packs" kicker={`${overview?.packs.filter((pack) => pack.installed).length ?? 0} INSTALLED`}>
        <ul className="engine-list">
          {overview?.packs.map((pack) => {
            const health = engineHealth[pack.id];
            return (
              <li key={pack.id}>
                <div>
                  <strong>{pack.name}</strong>
                  <span>{pack.capabilities.join(" · ")}</span>
                  {pack.installed ? (
                    <small>{health?.detail || "Checking local engine…"}</small>
                  ) : null}
                </div>
                <b
                  className={
                    health?.available && health.status === "ready"
                      ? "is-installed"
                      : ""
                  }
                >
                  {pack.installed
                    ? health?.status ?? "checking"
                    : "user-managed"}
                </b>
              </li>
            );
          })}
        </ul>
        {!overview?.packs.some((pack) => pack.installed) ? <Empty>No verified engine pack is installed. Optional templates cannot auto-download or execute installers.</Empty> : null}
      </Panel>
      <Panel title="Designed voice" kicker="SYNTHETIC">
        <form className="compact-form" onSubmit={submitProfile}>
          <div className="compact-form__row"><label>ID<input value={profileId} onChange={(event) => setProfileId(event.currentTarget.value)} required /></label><label>Name<input value={profileName} onChange={(event) => setProfileName(event.currentTarget.value)} required /></label></div>
          <label>Language<input value={language} onChange={(event) => setLanguage(event.currentTarget.value)} required /></label>
          <label>Voice design<textarea rows={3} value={description} onChange={(event) => setDescription(event.currentTarget.value)} required placeholder="Tone, pacing, timbre, and delivery." /></label>
          <label className="inline-toggle"><input type="checkbox" checked={publication} onChange={(event) => setPublication(event.currentTarget.checked)} />Authorize publication scope</label>
          <button className="button button--secondary" disabled={action.busy}>Save designed voice</button>
        </form>
      </Panel>
      <Panel title="Reference audio" kicker={`${overview?.references.length ?? 0} LOCAL WAV`}>
        <form className="compact-form" onSubmit={submitReference}>
          <label>
            WAV file
            <input
              type="file"
              accept=".wav,audio/wav,audio/x-wav"
              onChange={(event) =>
                setReferenceFile(event.currentTarget.files?.[0] ?? null)
              }
              required
            />
          </label>
          <label>
            Transcript (optional)
            <textarea
              rows={3}
              value={referenceTranscript}
              onChange={(event) =>
                setReferenceTranscript(event.currentTarget.value)
              }
              placeholder="Exact spoken words improve reference quality."
            />
          </label>
          <small>
            The WAV is read locally and sent only to the loopback Project Master
            backend. It is a voice sample, not proof of consent or a license.
            Browser source paths are never stored or displayed.
          </small>
          <button
            className="button button--secondary"
            disabled={!referenceFile || action.busy}
          >
            Import WAV
          </button>
        </form>
      </Panel>
      <Panel title="Reference voice rights" kicker="ATTESTED PROFILE">
        <form className="compact-form" onSubmit={submitReferenceProfile}>
          <div className="compact-form__row">
            <label>ID<input value={referenceProfileId} onChange={(event) => setReferenceProfileId(event.currentTarget.value)} required /></label>
            <label>Name<input value={referenceProfileName} onChange={(event) => setReferenceProfileName(event.currentTarget.value)} required /></label>
          </div>
          <label>Imported reference<select value={referenceArtifactId} onChange={(event) => setReferenceArtifactId(event.currentTarget.value)} required><option value="">Choose imported WAV</option>{overview?.references.map((reference) => <option value={reference.artifactId} key={reference.artifactId}>{reference.artifactId} · {reference.durationSeconds.toFixed(1)}s</option>)}</select></label>
          <label>Rights basis<select value={rightsBasis} onChange={(event) => { const next = event.currentTarget.value as typeof rightsBasis; setRightsBasis(next); if (next === "synthetic_reference" && subjectLabel === "My voice") setSubjectLabel("Synthetic generated voice"); if (next === "self_voice" && subjectLabel === "Synthetic generated voice") setSubjectLabel("My voice"); }}><option value="self_voice">My own voice</option><option value="synthetic_reference">Synthetic/generated audio — no real person</option><option value="explicit_consent" disabled>Explicit consent — evidence upload required</option><option value="licensed_voice" disabled>Licensed voice — evidence upload required</option></select></label>
          <label>Subject label<input value={subjectLabel} onChange={(event) => setSubjectLabel(event.currentTarget.value)} required /></label>
          <label>Attestation notes<textarea rows={2} value={rightsNotes} onChange={(event) => setRightsNotes(event.currentTarget.value)} placeholder="Where you keep the consent or license record; no document is uploaded here" /></label>
          <label className="inline-toggle"><input type="checkbox" checked={publication} onChange={(event) => setPublication(event.currentTarget.checked)} />Authorize publication scope</label>
          <label className="inline-toggle"><input type="checkbox" checked={commercial} onChange={(event) => setCommercial(event.currentTarget.checked)} />Authorize commercial use</label>
          <small>
            Saving records your attestation that the selected classification is
            accurate. Synthetic/generated means the WAV represents no real
            person; Project Master does not detect that automatically. The WAV
            is never treated as consent or license evidence. Evidence-backed
            consent/license creation remains unavailable in this desktop.
          </small>
          <button className="button button--primary" disabled={!referenceArtifactId || action.busy}>Create reference profile</button>
        </form>
      </Panel>
      <Panel title="Script project" kicker={`${overview?.projects.length ?? 0} PROJECTS`} wide>
        <form className="compact-form compact-form--script" onSubmit={submitProject}>
          <div className="compact-form__row"><label>ID<input value={projectId} onChange={(event) => setProjectId(event.currentTarget.value)} required /></label><label>Name<input value={projectName} onChange={(event) => setProjectName(event.currentTarget.value)} required /></label><label>Voice<select value={selectedProfile} onChange={(event) => setSelectedProfile(event.currentTarget.value)} required>{overview?.profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label></div>
          <label>Script<textarea rows={6} value={script} onChange={(event) => setScript(event.currentTarget.value)} required placeholder="Narration or dialogue to synthesize." /></label>
          <button className="button button--secondary" disabled={action.busy || !selectedProfile}>Save revision</button>
        </form>
      </Panel>
      <Panel title="Render control" kicker="LOCAL JOB">
        <div className="compact-form">
          <label>Project<select value={selectedProject} onChange={(event) => setSelectedProject(event.currentTarget.value)}>{overview?.projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label>
          <label>Engine pack<select value={selectedPack} onChange={(event) => setSelectedPack(event.currentTarget.value)}>{overview?.packs.filter((pack) => pack.installed).map((pack) => <option value={pack.id} key={pack.id}>{pack.name}</option>)}</select></label>
          <label>Purpose<select value={purpose} onChange={(event) => setPurpose(event.currentTarget.value as typeof purpose)}><option value="private">Private</option><option value="publication">Publication</option><option value="commercial">Commercial</option></select></label>
          <small>
            {selectedPackHealth
              ? `${selectedPackHealth.status}: ${selectedPackHealth.detail || "No engine detail."}`
              : "Select an installed engine and wait for its health check."}
          </small>
          <button
            className="button button--primary"
            disabled={
              !selectedProject ||
              !selectedPack ||
              !selectedPackHealth?.available ||
              selectedPackHealth.status !== "ready" ||
              action.busy
            }
            onClick={() =>
              void action.act(() =>
                createVoiceJob({
                  projectId: selectedProject,
                  packId: selectedPack,
                  purpose,
                }),
              )
            }
          >
            Create render
          </button>
        </div>
      </Panel>
      <Panel title="Render jobs" kicker={`${overview?.jobs.length ?? 0} JOBS`}>
        <ul className="job-list">
          {overview?.jobs.map((job) => {
            const health = engineHealth[job.enginePackId];
            const terminal = ["succeeded", "failed", "cancelled"].includes(
              job.status,
            );
            const launching = runningJobs.has(job.id);
            const cancelling = cancellingJobs.has(job.id);
            return (
              <li key={job.id}>
                <div>
                  <strong>{launching ? "running" : job.status}</strong>
                  <span>
                    {job.projectId} · {job.enginePackId}
                  </span>
                  {job.error ? <small>{job.error}</small> : null}
                </div>
                <div className="decision-actions">
                  {job.status === "planned" ? (
                    <button
                      disabled={
                        launching ||
                        cancelling ||
                        !health?.available ||
                        health.status !== "ready"
                      }
                      title={
                        health?.available
                          ? "Start this local render"
                          : "The selected engine is unavailable"
                      }
                      onClick={() => startVoiceRender(job.id)}
                    >
                      {launching ? "Starting…" : "Run"}
                    </button>
                  ) : null}
                  {!terminal || launching ? (
                    <button
                      disabled={cancelling}
                      onClick={() => void stopVoiceRender(job.id)}
                    >
                      {cancelling ? "Cancelling…" : "Cancel"}
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
        {!overview?.jobs.length ? <Empty>No render jobs.</Empty> : null}
      </Panel>
      <Panel title="Audio artifacts" kicker={`${overview?.artifacts.length ?? 0} VERIFIED`} wide>
        <div className="artifact-list">{overview?.artifacts.map((artifact) => <article key={artifact.id}><div><strong>{artifact.id}</strong><span>{artifact.format} · {artifact.durationSeconds.toFixed(1)}s · {(artifact.sizeBytes / 1024).toFixed(0)} KB</span></div><ArtifactPlayer artifactId={artifact.id} /></article>)}</div>
        {!overview?.artifacts.length ? <Empty>No completed audio artifacts.</Empty> : null}
      </Panel>
    </DashboardFrame>
  );
}

interface FeatureWorkspaceProps {
  workspace: Exclude<MasterWorkspace, "chat">;
  onReturnToCommand: () => void;
  selectedProjectId: string;
  onSelectProject: (projectId: string) => void;
  onProjectsChange: (projects: MasterProject[]) => void;
}

export function FeatureWorkspace({
  workspace,
  selectedProjectId,
  onSelectProject,
  onProjectsChange,
}: FeatureWorkspaceProps) {
  if (workspace === "dreams") return <DreamDashboard />;
  if (workspace === "creator") return <ComfyDashboard />;
  if (workspace === "voice") return <VoiceDashboard />;
  if (workspace === "projects") {
    return (
      <ProjectsDashboard
        selectedProject={selectedProjectId}
        onSelectProject={onSelectProject}
        onProjectsChange={onProjectsChange}
      />
    );
  }
  return <ApprovalsDashboard />;
}
