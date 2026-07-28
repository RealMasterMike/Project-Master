export type MasterWorkspace =
  | "chat"
  | "dreams"
  | "creator"
  | "voice"
  | "projects"
  | "approvals";

const WORKSPACES: Array<{ id: MasterWorkspace; label: string; marker?: string }> = [
  { id: "chat", label: "Command" },
  { id: "dreams", label: "Dream Lab", marker: "LAB" },
  { id: "creator", label: "Creator", marker: "COMFY" },
  { id: "voice", label: "Voice Studio", marker: "TTS" },
  { id: "projects", label: "Projects" },
  { id: "approvals", label: "Approvals" },
];

interface WorkspaceNavigationProps {
  active: MasterWorkspace;
  disabled: boolean;
  onChange: (workspace: MasterWorkspace) => void;
}

export function WorkspaceNavigation({
  active,
  disabled,
  onChange,
}: WorkspaceNavigationProps) {
  return (
    <nav className="workspace-navigation" aria-label="Project Master workspaces">
      {WORKSPACES.map((workspace) => (
        <button
          className={workspace.id === active ? "is-active" : undefined}
          type="button"
          key={workspace.id}
          aria-current={workspace.id === active ? "page" : undefined}
          disabled={disabled && workspace.id !== active}
          onClick={() => onChange(workspace.id)}
        >
          <span>{workspace.label}</span>
          {workspace.marker ? <small>{workspace.marker}</small> : null}
        </button>
      ))}
    </nav>
  );
}
