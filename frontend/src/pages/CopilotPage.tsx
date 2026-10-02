// CopilotPage: the grounded Enterprise Copilot chat (M6.5, Requirements 30, 31).
//
// Responsibilities:
//   - Ask a grounded question (`POST /api/copilot/ask`) and render the answer
//     with its citations (source type, title, evidence excerpt, deep link).
//   - Surface the fixed + dynamic suggested questions
//     (`GET /api/copilot/suggested-questions`, Requirement 30.6).
//   - Render an honest "insufficient evidence" message when the Copilot cannot
//     find enough confirmed evidence, rather than a guess (Requirement 30.5).
//   - For a DRAFT/ACT result, render the SUGGESTED artifact with a
//     confirm-before-mutate step (`POST /api/copilot/confirm`, Req 30.3, 31.3).
//
// The Copilot never mutates on ask; every artifact requires explicit human
// confirmation. Loading/empty/error states reuse the shared feedback surfaces.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, copilotApi } from "@/api";
import type {
  Citation,
  CopilotResponse,
  SuggestedArtifact,
} from "@/api";
import { ErrorState, SafetyRefusal } from "@/components/feedback";

interface ChatTurn {
  id: string;
  question: string;
  response: CopilotResponse | null;
  error: string | null;
  /** Set once a SUGGESTED artifact in this turn has been confirmed. */
  confirmation: string | null;
}

function turnId(): string {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Your session expired. Please sign in again.";
    return `The Copilot could not answer (${err.status}). Please retry.`;
  }
  return "The Copilot could not answer. Please retry.";
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        Citations
      </div>
      <ul className="mt-2 space-y-2">
        {citations.map((citation, index) => (
          <li
            key={citation.source_id}
            id={`citation-${citation.source_id}`}
            className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
          >
            <div className="flex items-center gap-2">
              <span className="rounded bg-brand-100 px-2 py-0.5 text-[11px] font-medium text-brand-700">
                [{index + 1}] {citation.source_type}
              </span>
              <span className="min-w-0 break-words text-sm font-medium text-slate-800">
                {citation.title}
              </span>
            </div>
            <p className="mt-1 line-clamp-3 text-xs text-slate-600">
              {citation.evidence_excerpt}
            </p>
            <Link
              to={citation.deep_link}
              className="mt-1 inline-block text-xs font-medium text-brand-600 hover:text-brand-700"
            >
              View source →
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Backend-validated citation links; no model-provided URLs enter the answer. */
export function AnswerText({ answer, citations }: { answer: string; citations: Citation[] }) {
  let text = answer;
  citations.forEach((citation, index) => {
    text = text.split(`(id=${citation.source_id})`).join(`[${index + 1}]`).split(citation.source_id).join(`[${index + 1}]`);
  });
  text = text.replace(/\n\s*[-*]?\s*(?:\*\*)?ID(?:\*\*)?\s*:\s*(\[\d+\])/gi, " $1");
  const inline = (value: string) => value.split(/(\*\*[^*]+\*\*|\[\d+\])/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    const number = /^\[(\d+)\]$/.exec(part);
    const citation = number ? citations[Number(number[1]) - 1] : undefined;
    return citation ? <Link key={index} to={citation.deep_link} title={citation.title} aria-label={`Source ${number![1]}: ${citation.title}`} className="font-medium text-blue-700 hover:underline">{part}</Link> : part;
  });
  // Render text as React nodes only. Never interpret model HTML or arbitrary URLs.
  const blocks: React.ReactNode[] = [];
  const lines = text.split(/\n/);
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim();
    if (!line) continue;
    const numbered = /^\d+\.\s+/.test(line);
    const bullet = /^[-*]\s+/.test(line);
    if (numbered || bullet) {
      const entries: React.ReactNode[] = [];
      const pattern = numbered ? /^\d+\.\s+/ : /^[-*]\s+/;
      const start = Number.parseInt(line, 10);
      let cursor = index;
      while (cursor < lines.length) {
        const current = lines[cursor].trim();
        if (!current && cursor + 1 < lines.length && pattern.test(lines[cursor + 1].trim())) { cursor++; continue; }
        if (!pattern.test(current)) break;
        entries.push(<li key={cursor}>{inline(current.replace(pattern, ""))}</li>);
        cursor++;
      }
      blocks.push(numbered ? <ol key={index} start={start} className="my-2 list-decimal space-y-2 pl-5">{entries}</ol> : <ul key={index} className="my-2 list-disc space-y-2 pl-5">{entries}</ul>);
      index = cursor - 1;
    } else blocks.push(<p className="my-2" key={index}>{inline(line)}</p>);
  }
  return <>{blocks}</>;
}

interface ArtifactCardProps {
  artifact: SuggestedArtifact;
  confirmation: string | null;
  busy: boolean;
  onConfirm: () => void;
}

function ArtifactCard({
  artifact,
  confirmation,
  busy,
  onConfirm,
}: ArtifactCardProps) {
  const canConfirm = artifact.kind === "ACTION_ITEM";
  return (
    <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="rounded bg-indigo-600 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">
          Suggested · {artifact.tier}
        </span>
        <span className="text-sm font-semibold text-indigo-900">
          {artifact.kind.replace(/_/g, " ")}
        </span>
      </div>
      {artifact.title ? (
        <p className="mt-2 text-sm font-medium text-slate-900">
          {artifact.title}
        </p>
      ) : null}
      {artifact.body ? (
        <p className="mt-1 whitespace-pre-line text-sm text-slate-700">
          {artifact.body}
        </p>
      ) : null}

      {confirmation ? (
        <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {confirmation}
        </p>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || !canConfirm}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? "Confirming…" : "Confirm & apply"}
          </button>
          {!canConfirm ? (
            <span className="text-xs text-slate-500">
              This suggestion is applied from its own workflow.
            </span>
          ) : (
            <span className="text-xs text-slate-500">
              Nothing changes until you confirm.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function AnswerCard({
  turn,
  busy,
  onConfirm,
}: {
  turn: ChatTurn;
  busy: boolean;
  onConfirm: (turn: ChatTurn) => void;
}) {
  const { response, error } = turn;

  return (
    <div className="space-y-2">
      <div className="ml-auto max-w-2xl rounded-lg bg-brand-600 px-4 py-2 text-sm text-white">
        {turn.question}
      </div>

      <div className="max-w-2xl rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        {error ? (
          <ErrorState message={error} variant="alert" />
        ) : response === null ? (
          <p className="text-sm text-slate-400">Thinking…</p>
        ) : response.insufficient_evidence ? (
          <SafetyRefusal
            variant="blocked"
            message={
              response.answer ??
              "I can't find enough confirmed evidence to answer that."
            }
          />
        ) : (
          <>
            {response.answer ? (
              <div className="break-words text-sm leading-relaxed text-slate-800">
                <AnswerText answer={response.answer} citations={response.citations} />
              </div>
            ) : null}
            <CitationList citations={response.citations} />
            {response.suggested_artifact ? (
              <ArtifactCard
                artifact={response.suggested_artifact}
                confirmation={turn.confirmation}
                busy={busy}
                onConfirm={() => onConfirm(turn)}
              />
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export default function CopilotPage({ embedded = false }: { embedded?: boolean }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [suggested, setSuggested] = useState<string[]>([]);
  const [asking, setAsking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  useLayoutEffect(() => {
    if (followLatest.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [turns]);


  useEffect(() => {
    let cancelled = false;
    void copilotApi
      .suggestedQuestions()
      .then((res) => {
        if (!cancelled) setSuggested(res.questions);
      })
      .catch(() => {
        if (!cancelled) setSuggested([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || asking) return;

      followLatest.current = true;
      const id = turnId();
      setTurns((prev) => [
        ...prev,
        { id, question: trimmed, response: null, error: null, confirmation: null },
      ]);
      setQuestion("");
      setAsking(true);
      try {
        const response = await copilotApi.ask({ question: trimmed });
        setTurns((prev) =>
          prev.map((turn) =>
            turn.id === id ? { ...turn, response } : turn,
          ),
        );
      } catch (err) {
        const message = describeError(err);
        setTurns((prev) =>
          prev.map((turn) => (turn.id === id ? { ...turn, error: message } : turn)),
        );
      } finally {
        setAsking(false);
      }
    },
    [asking],
  );

  const confirmArtifact = useCallback(async (turn: ChatTurn) => {
    const artifact = turn.response?.suggested_artifact;
    if (!artifact || confirming) return;
    setConfirming(true);
    try {
      const result = await copilotApi.confirm({
        kind: artifact.kind,
        title: artifact.title ?? undefined,
        body: artifact.body ?? undefined,
        evidence_text:
          typeof artifact.details["evidence_text"] === "string"
            ? (artifact.details["evidence_text"] as string)
            : (artifact.body ?? undefined),
      });
      setTurns((prev) =>
        prev.map((existing) =>
          existing.id === turn.id
            ? { ...existing, confirmation: result.message }
            : existing,
        ),
      );
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 422
          ? "This suggestion can't be applied here; use its own workflow."
          : "Could not apply the suggestion. Please retry.";
      setTurns((prev) =>
        prev.map((existing) =>
          existing.id === turn.id ? { ...existing, error: message } : existing,
        ),
      );
    } finally {
      setConfirming(false);
    }
  }, [confirming]);

  return (
    <div className={`mx-auto flex h-full min-h-0 max-w-3xl flex-col ${embedded ? "p-4" : "px-6 py-8"}`}>
      <header className="mb-3 shrink-0">
        {!embedded && <h1 className="text-xl font-semibold text-slate-900">Copilot</h1>}
        <p className={`mt-1 ${embedded ? "text-xs" : "text-sm"} text-slate-500`}>
          {embedded ? "Answers use saved workspace data. Open a citation to check its source." : "Ask about your saved, permitted data. Check the cited evidence; actions require your confirmation."}
        </p>
      </header>

      <div ref={transcript} role="log" aria-label="Copilot conversation" aria-live="polite" onScroll={() => {
        const node = transcript.current;
        if (node) followLatest.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      }} className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain pr-1">
      {turns.length === 0 ? (
        <section className="mb-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Try asking
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {suggested.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => void submit(q)}
                disabled={asking}
                className="rounded-full border border-slate-300 px-3 py-1.5 text-sm text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              >
                {q}
              </button>
            ))}
          </div>
        </section>
      ) : null}

        {turns.map((turn) => (
          <AnswerCard
            key={turn.id}
            turn={turn}
            busy={confirming}
            onConfirm={confirmArtifact}
          />
        ))}
      </div>

      <form
        className="mt-3 flex shrink-0 items-end gap-2 border-t border-slate-100 pt-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(question);
        }}
      >
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit(question);
            }
          }}
          rows={2}
          aria-label="Ask Copilot"
          placeholder="Ask a grounded question, or draft/propose something…"
          className="min-h-[44px] min-w-0 flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={asking || !question.trim()}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {asking ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
