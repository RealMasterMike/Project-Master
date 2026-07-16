import { useState } from "react";
import type {
  CommunicationFeedbackCategory,
  ProjectMasterCommunicationProfile,
} from "../lib/projectMasterApi";

interface CommunicationProfilePanelProps {
  profile: ProjectMasterCommunicationProfile | null;
  isLoading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSubmitFeedback: (
    category: CommunicationFeedbackCategory,
    note: string,
    scope: "global" | "situational",
  ) => Promise<void>;
}

const FEEDBACK_OPTIONS: Array<{ value: CommunicationFeedbackCategory; label: string }> = [
  { value: "preserve_semantic_fidelity", label: "Changed my meaning" },
  { value: "avoid_unjustified_assumptions", label: "Made an assumption" },
  { value: "avoid_unsolicited_advice", label: "Gave unwanted advice" },
  { value: "avoid_unnecessary_repetition", label: "Repeated itself" },
  { value: "use_context_before_interpreting", label: "Ignored conversation context" },
];

function sourceLabel(source: string): string {
  if (source === "explicit_user_feedback") return "Your feedback";
  if (source === "explicit_user_correction") return "Your correction";
  if (source === "explicit_user_instruction") return "Established rule";
  return "Observed";
}

export function CommunicationProfilePanel({
  profile,
  isLoading,
  error,
  onRefresh,
  onSubmitFeedback,
}: CommunicationProfilePanelProps) {
  const [category, setCategory] = useState<CommunicationFeedbackCategory>(
    "preserve_semantic_fidelity",
  );
  const [note, setNote] = useState("");
  const [scope, setScope] = useState<"global" | "situational">("global");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const activePreferences = profile?.preferences.filter((item) => item.status === "active") ?? [];

  async function saveFeedback(): Promise<void> {
    const trimmedNote = note.trim();
    if (!trimmedNote || isSaving) return;
    setIsSaving(true);
    setSubmitError(null);
    setSavedMessage(null);
    try {
      await onSubmitFeedback(category, trimmedNote, scope);
      setNote("");
      setSavedMessage(scope === "global" ? "Saved for future sessions." : "Saved for this context.");
    } catch (submissionError) {
      setSubmitError(submissionError instanceof Error ? submissionError.message : "Could not save feedback.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="communication-profile" role="tabpanel" aria-labelledby="customizer-tab-communication">
      <section className="communication-profile__section" aria-labelledby="communication-profile-heading">
        <div className="communication-profile__heading">
          <div>
            <span className="customizer-kicker">LOCAL PROFILE</span>
            <h3 id="communication-profile-heading">How MASTER communicates</h3>
          </div>
          <button className="icon-button" type="button" onClick={onRefresh} disabled={isLoading} title="Refresh profile" aria-label="Refresh communication profile">
            ↻
          </button>
        </div>
        <p>
          These are communication rules, not beliefs or facts about you. Corrections stay local and
          can be reviewed here.
        </p>

        {isLoading && !profile ? <p className="communication-profile__status">Loading profile…</p> : null}
        {error ? <p className="communication-profile__notice">{error}</p> : null}
        {profile ? (
          <>
            <div className="communication-profile__meta">
              <span>{profile.correctionsCount} recorded correction{profile.correctionsCount === 1 ? "" : "s"}</span>
              <span>{profile.dislikedResponsePatterns.length} guarded pattern{profile.dislikedResponsePatterns.length === 1 ? "" : "s"}</span>
            </div>
            <ul className="communication-preference-list">
              {activePreferences.map((preference) => (
                <li key={`${preference.key}-${preference.scope}`}>
                  <strong>{preference.value}</strong>
                  <span>{sourceLabel(preference.source)}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>

      <section className="communication-profile__section communication-profile__feedback" aria-labelledby="communication-feedback-heading">
        <span className="customizer-kicker">CORRECT MASTER</span>
        <h3 id="communication-feedback-heading">Record a communication failure</h3>
        <p>Use this when a response got the interaction wrong. It does not store the subject matter as memory.</p>

        <label>
          <span>What happened?</span>
          <select value={category} onChange={(event) => setCategory(event.currentTarget.value as CommunicationFeedbackCategory)}>
            {FEEDBACK_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Scope</span>
          <select value={scope} onChange={(event) => setScope(event.currentTarget.value as "global" | "situational")}>
            <option value="global">Future conversations</option>
            <option value="situational">This situation only</option>
          </select>
        </label>
        <label>
          <span>What should change?</span>
          <textarea
            value={note}
            maxLength={2000}
            placeholder="Example: Analyze the question first; do not turn it into a recommendation."
            onChange={(event) => setNote(event.currentTarget.value)}
          />
        </label>
        {submitError ? <p className="communication-profile__notice">{submitError}</p> : null}
        {savedMessage ? <p className="communication-profile__saved">{savedMessage}</p> : null}
        <button className="button button--secondary" type="button" onClick={() => void saveFeedback()} disabled={!note.trim() || isSaving}>
          {isSaving ? "Saving…" : "Save correction"}
        </button>
      </section>
    </div>
  );
}
