import { useState } from "react";
import { CommunicationProfilePanel } from "./CommunicationProfilePanel";
import type { LayoutDocument, LayoutOperation, SavedLayout } from "../lib/layout";
import type {
  CommunicationFeedbackCategory,
  ProjectMasterCommunicationProfile,
} from "../lib/projectMasterApi";

interface LayoutCustomizerProps {
  layout: LayoutDocument;
  canUndo: boolean;
  savedLayouts: SavedLayout[];
  onApplyOperations: (operations: LayoutOperation[]) => void;
  onUndo: () => void;
  onReset: () => void;
  onSave: (name: string) => void;
  onApplySaved: (id: string) => void;
  onDeleteSaved: (id: string) => void;
  communicationProfile: ProjectMasterCommunicationProfile | null;
  communicationLoading: boolean;
  communicationError: string | null;
  onRefreshCommunication: () => void;
  onSubmitCommunicationFeedback: (
    category: CommunicationFeedbackCategory,
    note: string,
    scope: "global" | "situational",
  ) => Promise<void>;
}

type CustomizerTab = "layout" | "saved" | "communication";

export function LayoutCustomizer({
  layout,
  canUndo,
  savedLayouts,
  onApplyOperations,
  onUndo,
  onReset,
  onSave,
  onApplySaved,
  onDeleteSaved,
  communicationProfile,
  communicationLoading,
  communicationError,
  onRefreshCommunication,
  onSubmitCommunicationFeedback,
}: LayoutCustomizerProps) {
  const [activeTab, setActiveTab] = useState<CustomizerTab>("layout");
  const [layoutName, setLayoutName] = useState("");
  const panel = layout.panels.customize_panel;
  const chatPanel = layout.panels.chat_panel;

  function setChatWidth(value: number): void {
    onApplyOperations([{ operation: "set_width", target: "chat_panel", value }]);
  }

  if (panel.collapsed) {
    return (
      <aside
        className="layout-customizer layout-customizer--collapsed"
        aria-label="Customize interface"
      >
        <div className="customizer-rail">
          <button
            className="customizer-rail-button"
            type="button"
            onClick={() =>
              onApplyOperations([
                { operation: "set_collapsed", target: "customize_panel", value: false },
              ])
            }
            aria-label="Expand interface customization"
            title="Expand interface customization"
          >
            <span aria-hidden="true">◀</span>
            <span>UI</span>
          </button>
          <div className="customizer-rail-actions">
            <button
              type="button"
              onClick={onUndo}
              disabled={!canUndo}
              title="Undo"
              aria-label="Undo layout change"
            >
              ↶
            </button>
            <button
              type="button"
              onClick={onReset}
              title="Reset default"
              aria-label="Reset default layout"
            >
              ⟳
            </button>
          </div>
        </div>
      </aside>
    );
  }

  function saveLayout(): void {
    const name = layoutName.trim();
    if (!name) return;
    onSave(name);
    setLayoutName("");
    setActiveTab("saved");
  }

  return (
    <aside className="layout-customizer" aria-label="Customize interface">
      <div className="customizer-header">
        <div>
          <span className="customizer-kicker">INTERFACE</span>
          <h2>Customize</h2>
        </div>
        <div className="customizer-header-actions">
          <button
            className="icon-button"
            type="button"
            onClick={() =>
              onApplyOperations([
                { operation: "set_collapsed", target: "customize_panel", value: true },
              ])
            }
            aria-label="Collapse customization panel"
            title="Collapse"
          >
            ▶
          </button>
          <button
            className="icon-button"
            type="button"
            onClick={() =>
              onApplyOperations([
                { operation: "set_visibility", target: "customize_panel", value: false },
              ])
            }
            aria-label="Close customization panel"
            title="Close"
          >
            ×
          </button>
        </div>
      </div>

      <div
        className="customizer-tabs"
        role="tablist"
        aria-label="Customization sections"
      >
        <button
          id="customizer-tab-layout"
          className={activeTab === "layout" ? "is-active" : ""}
          type="button"
          role="tab"
          aria-selected={activeTab === "layout"}
          aria-controls="customizer-panel-layout"
          onClick={() => setActiveTab("layout")}
        >
          Layout
        </button>
        <button
          id="customizer-tab-saved"
          className={activeTab === "saved" ? "is-active" : ""}
          type="button"
          role="tab"
          aria-selected={activeTab === "saved"}
          aria-controls="customizer-panel-saved"
          onClick={() => setActiveTab("saved")}
        >
          Saved
          {savedLayouts.length > 0 ? <span>{savedLayouts.length}</span> : null}
        </button>
        <button
          id="customizer-tab-communication"
          className={activeTab === "communication" ? "is-active" : ""}
          type="button"
          role="tab"
          aria-selected={activeTab === "communication"}
          aria-controls="customizer-panel-communication"
          onClick={() => setActiveTab("communication")}
        >
          Communication
        </button>
      </div>

      <div className="customizer-content">
        {activeTab === "layout" ? (
          <div
            id="customizer-panel-layout"
            className="customizer-section"
            role="tabpanel"
            aria-labelledby="customizer-tab-layout"
          >
            <label className="layout-field">
              <span>
                <strong>Chat width</strong>
                <output>{chatPanel.width}%</output>
              </span>
              <input
                type="range"
                min="55"
                max="100"
                step="5"
                value={chatPanel.width}
                onChange={(event) =>
                  onApplyOperations([
                    {
                      operation: "set_width",
                      target: "chat_panel",
                      value: Number(event.currentTarget.value),
                    },
                  ])
                }
              />
            </label>

            <label className="layout-field">
              <span>
                <strong>Panel width</strong>
                <output>{panel.width}px</output>
              </span>
              <input
                type="range"
                min="260"
                max="480"
                step="20"
                value={panel.width}
                onChange={(event) =>
                  onApplyOperations([
                    {
                      operation: "set_width",
                      target: "customize_panel",
                      value: Number(event.currentTarget.value),
                    },
                  ])
                }
              />
            </label>

            <div className="layout-presets" aria-label="Chat width presets">
              <button type="button" onClick={() => setChatWidth(65)}>
                Focused
              </button>
              <button type="button" onClick={() => setChatWidth(80)}>
                Balanced
              </button>
              <button type="button" onClick={() => setChatWidth(100)}>
                Wide
              </button>
            </div>

            <div className="save-layout-form">
              <label htmlFor="layout-name">Save current layout</label>
              <div>
                <input
                  id="layout-name"
                  value={layoutName}
                  maxLength={40}
                  placeholder="Layout name"
                  onChange={(event) => setLayoutName(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      saveLayout();
                    }
                  }}
                />
                <button
                  type="button"
                  onClick={saveLayout}
                  disabled={!layoutName.trim()}
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        ) : activeTab === "saved" ? (
          <div
            id="customizer-panel-saved"
            className="customizer-section"
            role="tabpanel"
            aria-labelledby="customizer-tab-saved"
          >
            {savedLayouts.length === 0 ? (
              <p className="saved-layouts-empty">No saved layouts yet.</p>
            ) : (
              <ul className="saved-layout-list">
                {savedLayouts.map((saved) => (
                  <li key={saved.id}>
                    <div>
                      <strong>{saved.name}</strong>
                      <span>{new Date(saved.createdAt).toLocaleDateString()}</span>
                    </div>
                    <div>
                      <button type="button" onClick={() => onApplySaved(saved.id)}>
                        Apply
                      </button>
                      <button
                        type="button"
                        onClick={() => onDeleteSaved(saved.id)}
                        aria-label={`Delete ${saved.name}`}
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div
            id="customizer-panel-communication"
            className="customizer-section customizer-section--communication"
            role="tabpanel"
            aria-labelledby="customizer-tab-communication"
          >
            <CommunicationProfilePanel
              profile={communicationProfile}
              isLoading={communicationLoading}
              error={communicationError}
              onRefresh={onRefreshCommunication}
              onSubmitFeedback={onSubmitCommunicationFeedback}
            />
          </div>
        )}
      </div>

      <div className="customizer-footer">
        <button type="button" onClick={onUndo} disabled={!canUndo}>
          Undo
        </button>
        <button type="button" onClick={onReset}>
          Reset default
        </button>
        <span>Revision {layout.revision}</span>
      </div>
    </aside>
  );
}
