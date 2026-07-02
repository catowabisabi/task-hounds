import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Clock3, HelpCircle } from "lucide-react";
import { apiGet, apiPost } from "../lib/api";

type QuestionOption = {
  label: string;
  description?: string;
};

type QuestionPrompt = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multiple?: boolean;
  custom?: boolean;
};

type PendingQuestion = {
  request_id: string;
  project_session_id?: string | null;
  role: string;
  questions: QuestionPrompt[];
  asked_at: string;
  deadline_at: string;
};

type PendingResponse = {
  questions: PendingQuestion[];
};

function secondsRemaining(deadline: string): number {
  const value = Date.parse(deadline);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.ceil((value - Date.now()) / 1000));
}

function formatCountdown(total: number): string {
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function OpenCodeQuestionModal() {
  const [pending, setPending] = useState<PendingQuestion[]>([]);
  const [answers, setAnswers] = useState<string[][]>([]);
  const [customAnswers, setCustomAnswers] = useState<string[]>([]);
  const [remaining, setRemaining] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const question = pending[0] ?? null;

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      const result = await apiGet<PendingResponse>("/api/opencode/questions").catch(() => null);
      if (!disposed && result) setPending(result.questions ?? []);
    };
    void load();
    const id = window.setInterval(() => void load(), 1000);
    return () => {
      disposed = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!question) {
      setAnswers([]);
      setCustomAnswers([]);
      setRemaining(0);
      return;
    }
    setAnswers(question.questions.map(() => []));
    setCustomAnswers(question.questions.map(() => ""));
    setError("");
    setRemaining(secondsRemaining(question.deadline_at));
    const id = window.setInterval(
      () => setRemaining(secondsRemaining(question.deadline_at)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [question?.request_id]);

  const finalAnswers = useMemo(
    () => (question?.questions ?? []).map((prompt, index) => {
      const custom = customAnswers[index]?.trim();
      if (custom) return prompt.multiple ? [...(answers[index] ?? []), custom] : [custom];
      return answers[index] ?? [];
    }),
    [answers, customAnswers, question],
  );
  const complete = !!question && finalAnswers.every(answer => answer.length > 0);

  if (!question) return null;

  const choose = (questionIndex: number, label: string, multiple: boolean) => {
    setAnswers(current => current.map((selected, index) => {
      if (index !== questionIndex) return selected;
      if (!multiple) return [label];
      return selected.includes(label)
        ? selected.filter(item => item !== label)
        : [...selected, label];
    }));
    if (!multiple) {
      setCustomAnswers(current => current.map((value, index) => index === questionIndex ? "" : value));
    }
  };

  const submit = async () => {
    if (!complete || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await apiPost(`/api/opencode/questions/${encodeURIComponent(question.request_id)}/answer`, {
        answers: finalAnswers,
      });
      setPending(current => current.filter(item => item.request_id !== question.request_id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[240] flex items-center justify-center px-4 py-6"
      style={{ background: "rgba(0,0,0,0.76)" }}
      role="dialog"
      aria-modal="true"
      aria-label={`${question.role} needs your answer`}
    >
      <div
        className="w-full max-w-2xl max-h-full overflow-y-auto rounded-lg shadow-2xl"
        style={{ background: "var(--bg-panel)", border: "1px solid var(--blue-dim)" }}
      >
        <div
          className="sticky top-0 z-10 flex items-start justify-between gap-4 px-5 py-4"
          style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex min-w-0 items-start gap-3">
            <HelpCircle size={18} className="mt-0.5 shrink-0" style={{ color: "var(--blue)" }} />
            <div className="min-w-0">
              <p className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                {question.role.replaceAll("_", " ")} needs your answer
              </p>
              <p className="mt-0.5 text-[10px] truncate" style={{ color: "var(--text-dim)" }}>
                {question.project_session_id || "Current OpenCode session"}
              </p>
            </div>
          </div>
          <div
            className="flex shrink-0 items-center gap-1.5 rounded px-2 py-1 text-[11px] font-mono"
            style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
            title="The first option for each unanswered question will be selected when the timer reaches zero."
          >
            <Clock3 size={13} />
            {formatCountdown(remaining)}
          </div>
        </div>

        <div className="space-y-5 p-5">
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            The agent is paused while it waits. Answer below to continue. If no answer is received within 15 minutes,
            Task Hounds will select the first option for each question automatically.
          </p>

          {question.questions.map((prompt, questionIndex) => {
            const options = Array.isArray(prompt.options) ? prompt.options : [];
            return (
              <fieldset key={`${question.request_id}-${questionIndex}`} className="space-y-2">
                <legend className="w-full">
                  {prompt.header && (
                    <span className="block text-[9px] font-semibold uppercase" style={{ color: "var(--blue)" }}>
                      {prompt.header}
                    </span>
                  )}
                  <span className="mt-1 block text-[12px] font-medium leading-relaxed" style={{ color: "var(--text-primary)" }}>
                    {prompt.question}
                  </span>
                  {prompt.multiple && (
                    <span className="mt-0.5 block text-[9px]" style={{ color: "var(--text-dim)" }}>
                      Select one or more
                    </span>
                  )}
                </legend>

                <div className="space-y-1.5">
                  {options.map(option => {
                    const checked = (answers[questionIndex] ?? []).includes(option.label);
                    return (
                      <label
                        key={option.label}
                        className="flex cursor-pointer items-start gap-2.5 rounded p-2.5"
                        style={{
                          background: checked ? "var(--blue-bg)" : "var(--bg-base)",
                          border: `1px solid ${checked ? "var(--blue-dim)" : "var(--border)"}`,
                        }}
                      >
                        <input
                          type={prompt.multiple ? "checkbox" : "radio"}
                          name={`opencode-question-${question.request_id}-${questionIndex}`}
                          aria-label={option.label}
                          checked={checked}
                          onChange={() => choose(questionIndex, option.label, !!prompt.multiple)}
                          className="mt-0.5 shrink-0"
                        />
                        <span className="min-w-0">
                          <span className="block text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>
                            {option.label}
                          </span>
                          {option.description && (
                            <span className="mt-0.5 block text-[10px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                              {option.description}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>

                {(prompt.custom !== false || options.length === 0) && (
                  <input
                    value={customAnswers[questionIndex] ?? ""}
                    onChange={event => {
                      const value = event.target.value;
                      setCustomAnswers(current => current.map((item, index) => index === questionIndex ? value : item));
                      if (value && !prompt.multiple) {
                        setAnswers(current => current.map((item, index) => index === questionIndex ? [] : item));
                      }
                    }}
                    placeholder="Type another answer..."
                    className="w-full rounded px-3 py-2 text-[11px] outline-none"
                    style={{ background: "var(--bg-base)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
                  />
                )}
              </fieldset>
            );
          })}

          {error && (
            <p className="rounded p-2 text-[10px]" style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}>
              {error}
            </p>
          )}
        </div>

        <div
          className="sticky bottom-0 flex items-center justify-between gap-3 px-5 py-3"
          style={{ background: "var(--bg-panel)", borderTop: "1px solid var(--border)" }}
        >
          <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>
            {pending.length > 1 ? `${pending.length - 1} more agent question${pending.length > 2 ? "s" : ""} waiting` : "The agent continues after submission"}
          </span>
          <button
            onClick={() => void submit()}
            disabled={!complete || submitting}
            className="rounded px-4 py-1.5 text-[11px] font-semibold disabled:opacity-40"
            style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}
          >
            {submitting ? "Sending..." : "Send answer"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
