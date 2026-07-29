import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  decideDreamItem,
  formatProjectMasterError,
  getDreamOverview,
  runManualDream,
  type DreamOverview,
  type MasterProject,
} from "../../lib/projectMasterApi";
import {
  Empty,
  Panel,
  Stamp,
  useBusyAction,
} from "../workspaces/DashboardPrimitives";

const ACTIVE_RUN_STATES = new Set(["claimed", "running"]);

function projectSourcePrefix(projectId: string): string {
  return `creator.${projectId}.`;
}

export function CreatorIdeas({ project }: { project?: MasterProject }) {
  const [overview, setOverview] = useState<DreamOverview | null>(null);
  const [topic, setTopic] = useState("");
  const [audience, setAudience] = useState("");
  const [platform, setPlatform] = useState("YouTube");
  const [tone, setTone] = useState("");
  const [goal, setGoal] = useState("");
  const [constraints, setConstraints] = useState("");
  const [directionCount, setDirectionCount] = useState(5);
  const [rationale, setRationale] = useState("");
  const ideaCardRefs = useRef<Record<string, HTMLElement | null>>({});

  const refresh = useCallback(async () => {
    setOverview(await getDreamOverview());
  }, []);
  const action = useBusyAction(refresh);

  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  const sourcePrefix = project ? projectSourcePrefix(project.id) : "";
  const projectIdeas = useMemo(
    () =>
      project
        ? overview?.inbox.filter(
        (item) =>
          item.recipeId === "creator-spark" &&
          item.sourceRefs.some((source) => source.startsWith(sourcePrefix)),
          ) ?? []
        : [],
    [overview, project, sourcePrefix],
  );
  const projectRuns = useMemo(
    () =>
      overview?.runs.filter(
        (run) =>
          run.recipeId === "creator-spark" &&
          run.windowKey.includes(`creator.${project?.id ?? ""}.`),
      ) ?? [],
    [overview, project?.id],
  );
  const hasActiveRun = projectRuns.some((run) =>
    ACTIVE_RUN_STATES.has(run.status),
  );

  useEffect(() => {
    if (!hasActiveRun) return;
    const interval = globalThis.setInterval(() => {
      void refresh().catch((caught) =>
        action.setError(formatProjectMasterError(caught)),
      );
    }, 4_000);
    return () => globalThis.clearInterval(interval);
  }, [action.setError, hasActiveRun, refresh]);

  function submitIdeas(event: FormEvent) {
    event.preventDefault();
    if (!project) return;
    const nonce = `${Date.now().toString(36)}.${Math.random()
      .toString(36)
      .slice(2, 10)}`;
    const sourceId = `${projectSourcePrefix(project.id)}${nonce}`.slice(0, 128);
    const requestId = sourceId;
    const brief = [
      `Creator project: ${project.name}`,
      `Topic or premise: ${topic.trim()}`,
      `Audience: ${audience.trim() || "Not specified"}`,
      `Primary platform: ${platform}`,
      `Tone: ${tone.trim() || "Choose what best fits the premise"}`,
      `Goal: ${goal.trim() || "Develop promising original content directions"}`,
      `Constraints: ${constraints.trim() || "None supplied"}`,
      "",
      `Produce ${directionCount} distinct directions. For each direction include a title, hook,`,
      "format, visual/audio approach, why it fits this audience, and one small next experiment.",
      "Keep uncertain assumptions explicit. These are speculative briefs for human review.",
    ].join("\n");
    void action.act(async () => {
      await runManualDream({
        recipeId: "creator-spark",
        sourceId,
        requestId,
        locator: `creator://${project.id}/ideas/${nonce}`,
        content: brief,
      });
    });
  }

  function decide(
    itemId: string,
    decision: "promote" | "reject",
  ) {
    void action
      .act(() =>
        decideDreamItem(
          itemId,
          decision,
          rationale.trim() ||
            (decision === "reject"
              ? "Not a fit for this Creator project."
              : "Selected as a media brief candidate."),
          "media_brief_candidate",
        ),
      )
      .then(() =>
        window.requestAnimationFrame(() => ideaCardRefs.current[itemId]?.focus()),
      );
  }

  return (
    <>
      <Panel title="Idea brief" kicker="CREATOR SPARK">
        {project ? (
          <form className="compact-form" onSubmit={submitIdeas}>
            <label>
              Topic or premise
              <textarea
                value={topic}
                onChange={(event) => setTopic(event.currentTarget.value)}
                placeholder="What should the content explore?"
                rows={3}
                required
              />
            </label>
            <div className="compact-form__row">
              <label>
                Audience
                <input
                  value={audience}
                  onChange={(event) => setAudience(event.currentTarget.value)}
                  placeholder="Who should care?"
                />
              </label>
              <label>
                Platform
                <select
                  value={platform}
                  onChange={(event) => setPlatform(event.currentTarget.value)}
                >
                  <option>YouTube</option>
                  <option>TikTok / Reels</option>
                  <option>Podcast</option>
                  <option>Blog / Newsletter</option>
                  <option>Campaign</option>
                  <option>Multi-platform</option>
                </select>
              </label>
              <label>
                Directions
                <input
                  type="number"
                  min={2}
                  max={12}
                  value={directionCount}
                  onChange={(event) =>
                    setDirectionCount(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
            </div>
            <div className="compact-form__row">
              <label>
                Tone
                <input
                  value={tone}
                  onChange={(event) => setTone(event.currentTarget.value)}
                  placeholder="Grounded, playful, cinematic…"
                />
              </label>
              <label>
                Goal
                <input
                  value={goal}
                  onChange={(event) => setGoal(event.currentTarget.value)}
                  placeholder="Educate, launch, entertain…"
                />
              </label>
            </div>
            <label>
              Constraints
              <textarea
                value={constraints}
                onChange={(event) => setConstraints(event.currentTarget.value)}
                placeholder="Runtime, budget, must-use elements, exclusions…"
                rows={2}
              />
            </label>
            <button
              className="button button--primary"
              disabled={action.busy || hasActiveRun}
            >
              {hasActiveRun ? "Generating directions…" : "Generate directions"}
            </button>
            <small>
              Runs locally through the review-only Creator Spark council.
              Nothing is published or added to a production queue.
            </small>
          </form>
        ) : (
          <Empty>Create or choose a Creator project before generating ideas.</Empty>
        )}
      </Panel>

      <Panel
        title="Idea board"
        kicker={`${projectIdeas.length} SAVED`}
        wide
      >
        {action.error ? (
          <div className="dashboard-alert" role="alert">
            {action.error}
          </div>
        ) : null}
        <label className="compact-label">
          Review rationale
          <input
            value={rationale}
            onChange={(event) => setRationale(event.currentTarget.value)}
            placeholder="Why this direction is or is not worth developing"
          />
        </label>
        {projectIdeas.length ? (
          <div className="proposal-list creator-idea-board">
            {projectIdeas.map((item) => (
              <article
                key={item.itemId}
                ref={(node) => {
                  ideaCardRefs.current[item.itemId] = node;
                }}
                tabIndex={-1}
              >
                <header>
                  <span>
                    {item.epistemicLabel.toUpperCase()} · {item.disposition}
                  </span>
                  <Stamp value={item.createdAt} />
                </header>
                <div className="creator-idea-copy">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {item.proposalText}
                  </ReactMarkdown>
                </div>
                {item.disposition === "pending" ? (
                  <div className="decision-actions">
                    <button
                      className="button button--secondary"
                      disabled={action.busy}
                      onClick={() => decide(item.itemId, "reject")}
                    >
                      Pass
                    </button>
                    <button
                      className="button button--primary"
                      disabled={action.busy || !rationale.trim()}
                      onClick={() => decide(item.itemId, "promote")}
                    >
                      Keep as media brief
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <Empty>
            No idea directions for this Creator project yet. Generate a brief
            above; completed results remain reviewable here.
          </Empty>
        )}
      </Panel>

      <Panel title="Idea runs" kicker={`${projectRuns.length} RUNS`}>
        <p
          className="visually-hidden"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {projectRuns[0]
            ? `Latest Creator idea run: ${projectRuns[0].status}.`
            : "No Creator idea runs for this project."}
        </p>
        {projectRuns.length ? (
          <ul className="status-list">
            {projectRuns.map((run) => (
              <li key={run.runId}>
                <span>
                  <Stamp value={run.createdAt} />
                </span>
                <strong>{run.status}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No Creator Spark runs for this project.</Empty>
        )}
      </Panel>
    </>
  );
}
