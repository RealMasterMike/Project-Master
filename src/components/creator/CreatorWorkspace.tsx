import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  cancelComfyJob,
  createProject,
  decideComfyWorkflow,
  formatProjectMasterError,
  getComfyOverview,
  getComfyProfileStatus,
  importComfyWorkflow,
  listProjects,
  refreshComfyJob,
  saveComfyProfile,
  type ComfyOverview,
  type ComfyProfileStatus,
  type ComfyWorkflowBinding,
  type ComfyWorkflowPurpose,
  type MasterProject,
} from "../../lib/projectMasterApi";
import {
  DashboardFrame,
  Empty,
  Panel,
  useBusyAction,
} from "../workspaces/DashboardPrimitives";
import { CreatorAIWorkspace } from "./CreatorAIWorkspace";
import { CreatorIdeas } from "./CreatorIdeas";
import { ComfyJobLedger } from "./ComfyJobLedger";
import { MediaLibrary } from "./MediaLibrary";
import { VideoTrimEditor } from "./VideoTrimEditor";

type CreatorSection =
  | "ideas"
  | "media"
  | "create"
  | "edit"
  | "utilities"
  | "workflows";

const CREATOR_SECTIONS: Array<{
  id: CreatorSection;
  label: string;
  description: string;
}> = [
  { id: "ideas", label: "Ideas", description: "Develop content directions" },
  { id: "media", label: "Media", description: "Browse project assets" },
  { id: "create", label: "Create", description: "Text → image or video" },
  { id: "edit", label: "AI Edit", description: "Image → image or video" },
  { id: "utilities", label: "Utilities", description: "Precise video trim" },
  { id: "workflows", label: "Workflows", description: "ComfyUI setup and trust" },
];

interface CreatorWorkspaceProps {
  selectedProjectId: string;
  onSelectProject: (projectId: string) => void;
  onProjectsChange: (projects: MasterProject[]) => void;
}

export function CreatorWorkspace({
  selectedProjectId,
  onSelectProject,
  onProjectsChange,
}: CreatorWorkspaceProps) {
  const [overview, setOverview] = useState<ComfyOverview | null>(null);
  const [projects, setProjects] = useState<MasterProject[]>([]);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [section, setSection] = useState<CreatorSection>("ideas");
  const [profileId, setProfileId] = useState("local-default");
  const [profileName, setProfileName] = useState("Local ComfyUI");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");
  const [trustedHosts, setTrustedHosts] = useState("");
  const [connection, setConnection] = useState<ComfyProfileStatus | null>(null);
  const [checkingConnection, setCheckingConnection] = useState(false);
  const [workflowName, setWorkflowName] = useState("");
  const [workflowJson, setWorkflowJson] = useState("");
  const [workflowPurpose, setWorkflowPurpose] =
    useState<ComfyWorkflowPurpose>("general");
  const [workflowBindings, setWorkflowBindings] = useState<
    ComfyWorkflowBinding[]
  >([]);
  const [decisionNote, setDecisionNote] = useState("");
  const [selectedProfile, setSelectedProfile] = useState("");
  const sectionButtonRefs = useRef<
    Partial<Record<CreatorSection, HTMLButtonElement | null>>
  >({});
  const refresh = useCallback(async () => {
    const [nextOverview, nextProjects, allProjects] = await Promise.all([
      getComfyOverview(),
      listProjects(undefined, "creator"),
      listProjects(),
    ]);
    setOverview(nextOverview);
    setProjects(nextProjects);
    onProjectsChange(allProjects);
    if (
      selectedProjectId &&
      !nextProjects.some((project) => project.id === selectedProjectId)
    ) {
      onSelectProject(nextProjects[0]?.id ?? "");
    } else if (!selectedProjectId && nextProjects.length) {
      onSelectProject(nextProjects[0].id);
    }
  }, [onProjectsChange, onSelectProject, selectedProjectId]);
  const action = useBusyAction(refresh);
  useEffect(() => {
    void action.act(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);
  useEffect(() => {
    const profiles = overview?.profiles ?? [];
    setSelectedProfile((current) =>
      profiles.some((item) => item.id === current)
        ? current
        : profiles[0]?.id ?? "",
    );
  }, [overview]);
  useEffect(() => {
    const profile = overview?.profiles.find(
      (item) => item.id === selectedProfile,
    );
    if (!profile) return;
    setProfileId(profile.id);
    setProfileName(profile.name);
    setBaseUrl(profile.baseUrl);
    setTrustedHosts(profile.trustedHosts.join(", "));
  }, [overview, selectedProfile]);
  useEffect(() => {
    let active = true;
    if (!selectedProfile) {
      setConnection(null);
      setCheckingConnection(false);
      return;
    }
    setCheckingConnection(true);
    void getComfyProfileStatus(selectedProfile)
      .then((status) => {
        if (active) setConnection(status);
      })
      .catch((caught) => {
        if (!active) return;
        setConnection({
          profileId: selectedProfile,
          ok: false,
          deviceCount: 0,
          objectTypeCount: 0,
          error: formatProjectMasterError(caught),
        });
      })
      .finally(() => {
        if (active) setCheckingConnection(false);
      });
    return () => {
      active = false;
    };
  }, [selectedProfile]);
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
      setConnection(await getComfyProfileStatus(profileId));
    });
  }

  function submitCreatorProject(event: FormEvent) {
    event.preventDefault();
    void action.act(async () => {
      const created = await createProject({
        name: projectName.trim(),
        description: projectDescription.trim(),
        projectType: "creator",
      });
      onSelectProject(created.id);
      setProjectName("");
      setProjectDescription("");
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
        workflowPurpose,
      );
      setWorkflowName("");
      setWorkflowJson("");
      setWorkflowBindings([]);
      setWorkflowPurpose("general");
    });
  }

  function addBinding(
    id:
      | "prompt"
      | "image_asset"
      | "seed"
      | "width"
      | "height"
      | "custom",
  ) {
    const base =
      id === "prompt"
        ? {
            id: "prompt",
            inputName: "text",
            valueType: "string" as const,
            description: "Positive prompt",
          }
        : id === "image_asset"
          ? {
              id: "source_image",
              inputName: "image",
              valueType: "image_asset" as const,
              description: "Verified project source image",
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

  const activeProject = projects.find(
    (project) => project.id === selectedProjectId,
  );

  function openCreatorSection(nextSection: CreatorSection) {
    setSection(nextSection);
    window.requestAnimationFrame(() =>
      sectionButtonRefs.current[nextSection]?.focus(),
    );
  }

  return (
    <DashboardFrame
      eyebrow="CREATOR STUDIO // IDEAS TO OUTPUT"
      title="Creator"
      description="Develop ideas, organize verified media, create or edit with approved local AI workflows, and use precise media utilities."
      status="Refresh"
      error={action.error}
      busy={action.busy}
      onRefresh={() => void action.act(async () => undefined)}
    >
      <Panel title="Creator project" kicker={`${projects.length} STUDIOS`} wide>
        <div className="creator-project-bar">
          <label>
            Active studio
            <select
              value={
                projects.some((project) => project.id === selectedProjectId)
                  ? selectedProjectId
                  : ""
              }
              onChange={(event) => onSelectProject(event.currentTarget.value)}
            >
              <option value="">Choose a Creator project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <form className="compact-form" onSubmit={submitCreatorProject}>
            <div className="compact-form__row">
              <label>
                New studio
                <input
                  value={projectName}
                  onChange={(event) =>
                    setProjectName(event.currentTarget.value)
                  }
                  placeholder="Campaign or channel name"
                  required
                />
              </label>
              <label>
                Purpose
                <input
                  value={projectDescription}
                  onChange={(event) =>
                    setProjectDescription(event.currentTarget.value)
                  }
                  placeholder="What this studio is creating"
                />
              </label>
              <button
                className="button button--primary"
                disabled={action.busy}
              >
                Create studio
              </button>
            </div>
          </form>
        </div>
      </Panel>
      <nav className="creator-sections" aria-label="Creator tools">
        {CREATOR_SECTIONS.map((item) => (
          <button
            ref={(node) => {
              sectionButtonRefs.current[item.id] = node;
            }}
            type="button"
            className={section === item.id ? "is-active" : undefined}
            aria-current={section === item.id ? "page" : undefined}
            key={item.id}
            onClick={() => setSection(item.id)}
          >
            <strong>{item.label}</strong>
            <span>{item.description}</span>
          </button>
        ))}
      </nav>
      {section === "ideas" ? <CreatorIdeas project={activeProject} /> : null}
      {section === "media" ? <MediaLibrary project={activeProject} /> : null}
      {section === "create" || section === "edit" ? (
        <>
          <CreatorAIWorkspace
            intent={section === "create" ? "create" : "edit"}
            project={activeProject}
            overview={overview}
            selectedProfile={selectedProfile}
            onSelectProfile={setSelectedProfile}
            onRefreshOverview={refresh}
            onViewMedia={() => openCreatorSection("media")}
            onOpenWorkflows={() => openCreatorSection("workflows")}
          />
          <ComfyJobLedger
            overview={overview}
            projects={projects}
            busy={action.busy}
            onRefreshJob={(jobId) =>
              action.act(() => refreshComfyJob(jobId))
            }
            onCancelJob={(jobId) =>
              action.act(() => cancelComfyJob(jobId))
            }
            onOpenMedia={(projectId) => {
              onSelectProject(projectId);
              openCreatorSection("media");
            }}
          />
        </>
      ) : null}
      {section === "utilities" ? (
        <VideoTrimEditor
          project={activeProject}
          onViewMedia={() => openCreatorSection("media")}
        />
      ) : null}
      {section === "workflows" ? (
        <>
      <Panel
        title="Connection profile"
        kicker={
          checkingConnection
            ? "CHECKING"
            : connection?.ok
              ? "CONNECTED"
              : overview?.profiles.length
                ? "CONFIGURED"
                : "OFFLINE DEFAULT"
        }
      >
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
            <button
              className="button button--secondary"
              type="button"
              disabled={!selectedProfile || action.busy || checkingConnection}
              onClick={() => {
                setCheckingConnection(true);
                void getComfyProfileStatus(selectedProfile)
                  .then(setConnection)
                  .catch((caught) =>
                    setConnection({
                      profileId: selectedProfile,
                      ok: false,
                      deviceCount: 0,
                      objectTypeCount: 0,
                      error: formatProjectMasterError(caught),
                    }),
                  )
                  .finally(() => setCheckingConnection(false));
              }}
            >
              Test selected
            </button>
          </div>
          {checkingConnection ? (
            <span className="form-status" role="status" aria-live="polite">
              Checking the selected endpoint…
            </span>
          ) : connection ? (
            <span
              className="form-status"
              role={connection.ok ? "status" : "alert"}
              aria-live="polite"
            >
              {connection.ok
                ? `Connected · ${connection.deviceCount} device${connection.deviceCount === 1 ? "" : "s"} · ${connection.objectTypeCount} node types`
                : connection.error || "Offline"}
            </span>
          ) : null}
        </form>
      </Panel>
      <Panel title="Import API workflow" kicker="IMMUTABLE REVISION">
        <form className="compact-form" onSubmit={submitWorkflow}>
          <div className="compact-form__row">
            <label>
              Workflow name
              <input
                value={workflowName}
                onChange={(event) =>
                  setWorkflowName(event.currentTarget.value)
                }
                placeholder="Workflow name"
                required
              />
            </label>
            <label>
              Output type
              <select
                value={workflowPurpose}
                onChange={(event) =>
                  setWorkflowPurpose(
                    event.currentTarget.value as ComfyWorkflowPurpose,
                  )
                }
              >
                <option value="general">General / mixed</option>
                <option value="image">Image</option>
                <option value="video">Video</option>
                <option value="audio">Audio</option>
              </select>
            </label>
          </div>
          <textarea aria-label="ComfyUI API workflow JSON" value={workflowJson} onChange={(event) => setWorkflowJson(event.currentTarget.value)} rows={7} placeholder='Paste ComfyUI "Save (API Format)" JSON' required />
          <div className="binding-toolbar">
            <span>Safe job inputs</span>
            {([
              "prompt",
              "image_asset",
              "seed",
              "width",
              "height",
              "custom",
            ] as const).map(
              (preset) => (
                <button
                  className="button button--secondary"
                  type="button"
                  key={preset}
                  onClick={() => addBinding(preset)}
                >
                  + {preset === "image_asset" ? "source image" : preset}
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
                    onChange={(event) => {
                      const valueType = event.currentTarget
                        .value as ComfyWorkflowBinding["valueType"];
                      updateBinding(index, {
                        valueType,
                        required:
                          valueType === "image_asset"
                            ? true
                            : binding.required,
                        defaultValue:
                          valueType === "image_asset"
                            ? undefined
                            : binding.defaultValue,
                        minimum: ["integer", "number"].includes(valueType)
                          ? binding.minimum
                          : undefined,
                        maximum: ["integer", "number"].includes(valueType)
                          ? binding.maximum
                          : undefined,
                        choices:
                          valueType === "enum" ? binding.choices : [],
                      });
                    }}
                  >
                    <option value="string">Text</option>
                    <option value="integer">Integer</option>
                    <option value="number">Number</option>
                    <option value="boolean">On/off</option>
                    <option value="enum">Choice</option>
                    <option value="image_asset">Verified project image</option>
                  </select>
                </label>
              </div>
              <div className="compact-form__row">
                <label>
                  Default
                  <input
                    disabled={binding.valueType === "image_asset"}
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
                    disabled={binding.valueType === "image_asset"}
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
            Output type is immutable revision metadata and does not infer or
            install models.
          </small>
          <button className="button button--secondary" disabled={action.busy}>Validate and import</button>
        </form>
      </Panel>
      <Panel title="Workflow trust" kicker={`${overview?.workflows.length ?? 0} REVISIONS`} wide>
        <label className="compact-label">Decision note<input value={decisionNote} onChange={(event) => setDecisionNote(event.currentTarget.value)} placeholder="Review finding" /></label>
        <div className="workflow-list">
          {overview?.workflows.map((workflow) => (
            <article key={workflow.id}>
              <div>
                <div className="workflow-list__labels">
                  <span>{workflow.trustState.toUpperCase()}</span>
                  <span
                    className={`workflow-purpose-badge ${
                      workflow.curatedDefault ? "is-general" : ""
                    }`}
                  >
                    {workflow.curatedDefault
                      ? "curated default"
                      : "manual / unverified"}
                  </span>
                  <span
                    className={`workflow-purpose-badge is-${workflow.purpose}`}
                  >
                    {workflow.purpose}
                  </span>
                </div>
                <strong>{workflow.name}</strong>
                <code>{workflow.digest.slice(0, 16)}</code>
              </div>
              <div className="decision-actions">
                <button className="button button--secondary" disabled={action.busy} onClick={() => void action.act(() => decideComfyWorkflow(workflow.id, "rejected", decisionNote || "Rejected during review."))}>Reject</button>
                <button className="button button--primary" disabled={action.busy || !decisionNote.trim()} onClick={() => void action.act(() => decideComfyWorkflow(workflow.id, "approved", decisionNote.trim()))}>Approve revision</button>
              </div>
            </article>
          ))}
          {!overview?.workflows.length ? <Empty>No workflow revisions imported.</Empty> : null}
        </div>
      </Panel>
        </>
      ) : null}
    </DashboardFrame>
  );
}
