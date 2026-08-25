// SourceInboxPage: the "Collect → Classify → Connect" surface of the pipeline.
//
// Responsibilities (Requirements 3, 4, 5, 6):
//   - Add a raw source item (Req 3.1) and list the organization's inbox,
//     filterable by status and business category (Req 3.2).
//   - Per item: run AI classification (Req 4.1), then confirm (optionally with
//     an override) or reject the suggestion (Req 5.1, 5.2, 5.3).
//   - Extract structured knowledge once a classification is confirmed (Req 6).
//   - Enforce the BLOCKING highly-sensitive acknowledgment gate: extraction of
//     HIGHLY_SENSITIVE content is refused (409) until the user explicitly
//     acknowledges, after which extract is retried with acknowledged=true
//     (Req 6.4, 6.5).
//
// All data flows through the real Core Engine API (src/api/sourceItems.ts);
// there is no hardcoded output.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, getErrorMessage, parseFieldErrors, sourceItemsApi } from "@/api";
import type {
  BusinessCategory,
  ClassificationOverride,
  FieldErrors,
  KnowledgeItem,
  Relevance,
  Sensitivity,
  SourceItem,
  SourceItemDetail,
  SourceStatus,
  SourceType,
} from "@/api";
import SuggestionCard from "@/components/SuggestionCard";
import EvidenceBadge from "@/components/EvidenceBadge";
import {
  EmptyState,
  ErrorState,
  FieldError,
  LoadingState,
  SafetyRefusal,
} from "@/components/feedback";

const SOURCE_TYPES: SourceType[] = [
  "EMAIL",
  "DOCUMENT",
  "MEETING_NOTE",
  "TASK_NOTE",
  "CHAT",
  "MANUAL",
];

const SOURCE_STATUSES: SourceStatus[] = [
  "NEW",
  "CLASSIFIED",
  "PROCESSED",
  "ARCHIVED",
  "DISMISSED",
];

const RELEVANCES: Relevance[] = [
  "WORK_RELATED",
  "PERSONAL",
  "IRRELEVANT",
  "SPAM",
  "SYSTEM_NOTIFICATION",
];

const BUSINESS_CATEGORIES: BusinessCategory[] = [
  "CLIENT",
  "PROJECT",
  "DECISION",
  "TASK",
  "RISK",
  "MEETING",
  "PARTNER",
  "LEARNING",
  "OTHER",
];

const SENSITIVITIES: Sensitivity[] = [
  "PUBLIC",
  "INTERNAL",
  "CONFIDENTIAL",
  "HIGHLY_SENSITIVE",
];

function humanize(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

const STATUS_PILL: Record<SourceStatus, string> = {
  NEW: "bg-sky-50 text-sky-700 border-sky-200",
  CLASSIFIED: "bg-indigo-50 text-indigo-700 border-indigo-200",
  PROCESSED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  ARCHIVED: "bg-slate-100 text-slate-600 border-slate-200",
  DISMISSED: "bg-slate-100 text-slate-500 border-slate-200",
};

// ---------------------------------------------------------------------------
// Add source item form
// ---------------------------------------------------------------------------

interface AddSourceItemFormProps {
  onCreated: (item: SourceItem) => void;
}

function AddSourceItemForm({ onCreated }: AddSourceItemFormProps) {
  const [sourceType, setSourceType] = useState<SourceType>("EMAIL");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setFieldErrors({});
    setSubmitting(true);
    try {
      const created = await sourceItemsApi.create({
        source_type: sourceType,
        title: title.trim(),
        content: content.trim(),
      });
      onCreated(created);
      setTitle("");
      setContent("");
      setSourceType("EMAIL");
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fields = parseFieldErrors(err);
        setFieldErrors(fields);
        setError(
          Object.keys(fields).length > 0
            ? getErrorMessage(err)
            : "Please provide a title and content before adding.",
        );
      } else {
        setError(getErrorMessage(err, "Something went wrong while adding the item. Please retry."));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = title.trim() !== "" && content.trim() !== "" && !submitting;

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-sm font-semibold text-slate-900">Add source item</h2>
      <p className="mt-1 text-xs text-slate-500">
        Collect raw content into the inbox. It starts in status NEW awaiting
        classification.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="source-type"
            className="block text-sm font-medium text-slate-700"
          >
            Source type
          </label>
          <select
            id="source-type"
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value as SourceType)}
            disabled={submitting}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
          >
            {SOURCE_TYPES.map((type) => (
              <option key={type} value={type}>
                {humanize(type)}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="source-title"
            className="block text-sm font-medium text-slate-700"
          >
            Title
          </label>
          <input
            id="source-title"
            type="text"
            value={title}
            maxLength={512}
            onChange={(e) => setTitle(e.target.value)}
            disabled={submitting}
            placeholder="Short subject line"
            aria-invalid={Boolean(fieldErrors.title)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
          />
          <FieldError message={fieldErrors.title} />
        </div>
      </div>

      <div className="mt-4">
        <label
          htmlFor="source-content"
          className="block text-sm font-medium text-slate-700"
        >
          Content
        </label>
        <textarea
          id="source-content"
          value={content}
          rows={4}
          onChange={(e) => setContent(e.target.value)}
          disabled={submitting}
          placeholder="Paste the raw email, note, or document text…"
          aria-invalid={Boolean(fieldErrors.content)}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
        />
        <FieldError message={fieldErrors.content} />
      </div>

      {error ? <ErrorState message={error} variant="alert" className="mt-3" /> : null}

      <div className="mt-4">
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Adding…" : "Add to inbox"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Source item card (with classify / confirm / reject / extract)
// ---------------------------------------------------------------------------

interface SourceItemCardProps {
  item: SourceItem;
  onItemChanged: (item: SourceItem) => void;
  onDeleted: (itemId: string) => void;
}

function SourceItemCard({ item, onItemChanged, onDeleted }: SourceItemCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<SourceItemDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailRequestGeneration = useRef(0);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Classification override selectors (empty string = keep AI value).
  const [overrideRelevance, setOverrideRelevance] = useState<Relevance | "">("");
  const [overrideCategory, setOverrideCategory] = useState<BusinessCategory | "">("");
  const [overrideSensitivity, setOverrideSensitivity] = useState<Sensitivity | "">("");

  // Knowledge extraction state.
  const [knowledge, setKnowledge] = useState<KnowledgeItem | null>(null);
  // When the sensitivity gate refuses extraction, this holds the blocking
  // acknowledgment prompt until the user explicitly acknowledges (Req 6.4).
  const [ackRequired, setAckRequired] = useState(false);
  const [extractRefusal, setExtractRefusal] = useState<string | null>(null);

  const loadDetail = useCallback(async () => {
    const requestGeneration = ++detailRequestGeneration.current;
    setLoadingDetail(true);
    setDetailError(null);
    try {
      const result = await sourceItemsApi.get(item.id);
      if (requestGeneration !== detailRequestGeneration.current) return;
      setDetail(result);
      // Keep the parent list in sync with any status change.
      onItemChanged(result.source_item);
    } catch {
      if (requestGeneration !== detailRequestGeneration.current) return;
      setDetailError("Could not load item details. Please retry.");
    } finally {
      if (requestGeneration === detailRequestGeneration.current) {
        setLoadingDetail(false);
      }
    }
  }, [item.id, onItemChanged]);

  useEffect(() => {
    return () => {
      detailRequestGeneration.current += 1;
    };
  }, [item.id]);

  function toggleExpanded() {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null && !loadingDetail) {
      void loadDetail();
    }
  }

  const classification = detail?.classification ?? null;
  const isConfirmed = classification?.status === "CONFIRMED";
  const isSuggested = classification?.status === "SUGGESTED";
  const isHighlySensitive =
    classification?.sensitivity === "HIGHLY_SENSITIVE";

  async function runAction<T>(fn: () => Promise<T>): Promise<T | undefined> {
    if (busy) return undefined;
    setBusy(true);
    setActionError(null);
    try {
      return await fn();
    } catch (err) {
      setActionError(getErrorMessage(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function handleClassify() {
    await runAction(async () => {
      await sourceItemsApi.classify(item.id);
      await loadDetail();
    });
  }

  async function handleConfirm() {
    const override: ClassificationOverride = {};
    if (overrideRelevance) override.relevance = overrideRelevance;
    if (overrideCategory) override.business_category = overrideCategory;
    if (overrideSensitivity) override.sensitivity = overrideSensitivity;
    const hasOverride = Object.keys(override).length > 0;

    await runAction(async () => {
      await sourceItemsApi.confirmClassification(
        item.id,
        hasOverride ? override : undefined,
      );
      await loadDetail();
    });
  }

  async function handleReject() {
    await runAction(async () => {
      await sourceItemsApi.rejectClassification(item.id);
      await loadDetail();
    });
  }

  async function handleDismiss() {
    await runAction(async () => {
      const updated = await sourceItemsApi.dismiss(item.id);
      onItemChanged(updated);
      await loadDetail();
    });
  }

  async function handleDelete() {
    if (busy) return;
    const confirmed = window.confirm(
      `Permanently delete "${item.title}"? This also deletes its classification and detaches any derived knowledge. This cannot be undone.`,
    );
    if (!confirmed) return;
    await runAction(async () => {
      await sourceItemsApi.remove(item.id);
      onDeleted(item.id);
    });
  }

  // Extraction. `acknowledged` is only sent once the user has explicitly
  // acknowledged the highly-sensitive gate. A 409 refusal is surfaced clearly;
  // when it is the sensitivity gate, we raise the blocking acknowledgment
  // banner instead of silently retrying (Req 6.4).
  async function handleExtract(acknowledged: boolean) {
    setExtractRefusal(null);
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await sourceItemsApi.extract(
        item.id,
        acknowledged ? true : undefined,
      );
      setKnowledge(result);
      setAckRequired(false);
      await loadDetail();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        if (isHighlySensitive && !acknowledged) {
          // Sensitivity gate: require explicit acknowledgment before retrying.
          setAckRequired(true);
          setExtractRefusal(
            "This item is HIGHLY SENSITIVE. Extraction is blocked until you acknowledge handling this content.",
          );
        } else {
          // Privacy gate or other conflict — cannot proceed.
          setExtractRefusal(
            getErrorMessage(
              err,
              "Extraction was refused for this item. Its category or sensitivity prevents knowledge extraction.",
            ),
          );
        }
      } else {
        setActionError(getErrorMessage(err, "Something went wrong during extraction. Please retry."));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        className="flex w-full items-start justify-between gap-3 px-5 py-4 text-left"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
              {humanize(item.source_type)}
            </span>
            <span
              className={`rounded border px-2 py-0.5 text-xs font-medium ${STATUS_PILL[item.status]}`}
            >
              {item.status}
            </span>
          </div>
          <h3 className="mt-2 truncate text-sm font-semibold text-slate-900">
            {item.title}
          </h3>
          <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">
            {item.content}
          </p>
        </div>
        <span className="shrink-0 text-xs font-medium text-brand-600">
          {expanded ? "Hide" : "Open"}
        </span>
      </button>

      {expanded ? (
        <div className="border-t border-slate-100 px-5 py-4">
          {loadingDetail ? (
            <LoadingState label="Loading details…" />
          ) : detailError ? (
            <ErrorState message={detailError} onRetry={() => void loadDetail()} />
          ) : (
            <div className="space-y-4">
              {/* Full content */}
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Content
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                  {detail?.source_item.content ?? item.content}
                </p>
              </div>

              {/* Classification */}
              {classification ? (
                <SuggestionCard
                  title={`${humanize(classification.business_category)} · ${humanize(classification.relevance)}`}
                  subtitle={`Sensitivity: ${humanize(classification.sensitivity)}`}
                  confidence={classification.confidence}
                  reasons={classification.reasons}
                  evidenceSpans={classification.evidence_spans}
                  status={classification.status}
                  onConfirm={isSuggested ? handleConfirm : undefined}
                  onReject={isSuggested ? handleReject : undefined}
                  confirmLabel="Confirm classification"
                  rejectLabel="Reject"
                  busy={busy}
                >
                  {isSuggested ? (
                    <div className="grid gap-3 sm:grid-cols-3">
                      <label className="text-xs font-medium text-slate-600">
                        Override relevance
                        <select
                          value={overrideRelevance}
                          onChange={(e) =>
                            setOverrideRelevance(e.target.value as Relevance | "")
                          }
                          disabled={busy}
                          className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
                        >
                          <option value="">Keep AI value</option>
                          {RELEVANCES.map((rel) => (
                            <option key={rel} value={rel}>
                              {humanize(rel)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="text-xs font-medium text-slate-600">
                        Override category
                        <select
                          value={overrideCategory}
                          onChange={(e) =>
                            setOverrideCategory(
                              e.target.value as BusinessCategory | "",
                            )
                          }
                          disabled={busy}
                          className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
                        >
                          <option value="">Keep AI value</option>
                          {BUSINESS_CATEGORIES.map((cat) => (
                            <option key={cat} value={cat}>
                              {humanize(cat)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="text-xs font-medium text-slate-600">
                        Override sensitivity
                        <select
                          value={overrideSensitivity}
                          onChange={(e) =>
                            setOverrideSensitivity(
                              e.target.value as Sensitivity | "",
                            )
                          }
                          disabled={busy}
                          className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-slate-50"
                        >
                          <option value="">Keep AI value</option>
                          {SENSITIVITIES.map((sen) => (
                            <option key={sen} value={sen}>
                              {humanize(sen)}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  ) : null}
                </SuggestionCard>
              ) : (
                <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3">
                  <p className="text-sm text-slate-600">
                    No classification yet. Run AI classification to get a
                    suggestion.
                  </p>
                  <button
                    type="button"
                    onClick={handleClassify}
                    disabled={busy}
                    className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {busy ? "Classifying…" : "Classify"}
                  </button>
                </div>
              )}

              {/* Knowledge extraction (only after a confirmed classification) */}
              {isConfirmed ? (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-900">
                        Knowledge extraction
                      </h4>
                      <p className="mt-0.5 text-xs text-slate-500">
                        Connect this item into evidence-backed knowledge.
                      </p>
                    </div>
                    {!ackRequired ? (
                      <button
                        type="button"
                        onClick={() => void handleExtract(false)}
                        disabled={busy}
                        className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busy ? "Extracting…" : "Extract knowledge"}
                      </button>
                    ) : null}
                  </div>

                  {/* Blocking highly-sensitive acknowledgment banner (Req 6.4) */}
                  {ackRequired ? (
                    <SafetyRefusal
                      variant="acknowledge"
                      message={
                        extractRefusal ??
                        "This content is highly sensitive and cannot be processed without your explicit acknowledgment."
                      }
                      busy={busy}
                      onAcknowledge={() => void handleExtract(true)}
                      onCancel={() => {
                        setAckRequired(false);
                        setExtractRefusal(null);
                      }}
                      className="mt-3"
                    />
                  ) : null}

                  {/* Non-blocking refusal (e.g. privacy gate) */}
                  {!ackRequired && extractRefusal ? (
                    <SafetyRefusal
                      variant="blocked"
                      message={extractRefusal}
                      className="mt-3"
                    />
                  ) : null}

                  {knowledge ? (
                    <div className="mt-3 rounded-md border border-emerald-200 bg-white p-3">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-emerald-700">
                          Suggested knowledge
                        </span>
                        <span className="text-xs text-slate-500">
                          {knowledge.status}
                        </span>
                      </div>
                      <p className="mt-2 text-sm font-medium text-slate-900">
                        {knowledge.summary}
                      </p>
                      {knowledge.key_points.length > 0 ? (
                        <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-slate-600">
                          {knowledge.key_points.map((point, index) => (
                            <li key={`${point}-${index}`}>{point}</li>
                          ))}
                        </ul>
                      ) : null}
                      <EvidenceBadge
                        text={knowledge.evidence_text}
                        className="mt-2"
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}

              {actionError ? (
                <ErrorState message={actionError} variant="alert" />
              ) : null}

              {/* Secondary actions */}
              <div className="flex justify-end gap-4">
                {item.status !== "DISMISSED" ? (
                  <button
                    type="button"
                    onClick={handleDismiss}
                    disabled={busy}
                    className="text-xs font-medium text-slate-400 underline-offset-2 transition hover:text-slate-600 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Dismiss item
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={handleDelete}
                  disabled={busy}
                  className="text-xs font-medium text-red-500 underline-offset-2 transition hover:text-red-700 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Delete permanently
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SourceInboxPage() {
  const [items, setItems] = useState<SourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const listRequestGeneration = useRef(0);

  const [statusFilter, setStatusFilter] = useState<SourceStatus | "">("");
  const [categoryFilter, setCategoryFilter] = useState<BusinessCategory | "">("");

  const filters = useMemo(
    () => ({
      status: statusFilter || undefined,
      category: categoryFilter || undefined,
    }),
    [statusFilter, categoryFilter],
  );

  const loadItems = useCallback(async () => {
    const requestGeneration = ++listRequestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const result = await sourceItemsApi.list(filters);
      if (requestGeneration !== listRequestGeneration.current) return;
      setItems(result);
    } catch {
      if (requestGeneration !== listRequestGeneration.current) return;
      setError("Could not load the inbox. Please retry.");
    } finally {
      if (requestGeneration === listRequestGeneration.current) {
        setLoading(false);
      }
    }
  }, [filters]);

  useEffect(() => {
    void loadItems();
    return () => {
      listRequestGeneration.current += 1;
    };
  }, [loadItems]);

  const handleCreated = useCallback((item: SourceItem) => {
    // Prepend the new item so it appears immediately without a full reload.
    setItems((current) => [item, ...current]);
  }, []);

  const handleItemChanged = useCallback((updated: SourceItem) => {
    setItems((current) =>
      current.map((it) => (it.id === updated.id ? updated : it)),
    );
  }, []);

  const handleDeleted = useCallback((itemId: string) => {
    setItems((current) => current.filter((it) => it.id !== itemId));
  }, []);

  return (
    <section className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Source Inbox</h1>
        <p className="mt-1 text-sm text-slate-500">
          Collect raw content, run AI classification, confirm suggestions, and
          extract evidence-backed knowledge.
        </p>
      </header>

      <AddSourceItemForm onCreated={handleCreated} />

      {/* Filters (Requirement 3.2) */}
      <div className="mt-6 flex flex-wrap items-end gap-4">
        <div>
          <label
            htmlFor="filter-status"
            className="block text-xs font-medium text-slate-600"
          >
            Status
          </label>
          <select
            id="filter-status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as SourceStatus | "")}
            className="mt-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">All statuses</option>
            {SOURCE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="filter-category"
            className="block text-xs font-medium text-slate-600"
          >
            Category
          </label>
          <select
            id="filter-category"
            value={categoryFilter}
            onChange={(e) =>
              setCategoryFilter(e.target.value as BusinessCategory | "")
            }
            className="mt-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">All categories</option>
            {BUSINESS_CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>
                {humanize(cat)}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          onClick={() => void loadItems()}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
        >
          Refresh
        </button>
      </div>

      {/* List */}
      <div className="mt-4">
        {loading ? (
          <LoadingState variant="skeleton" label="Loading inbox…" />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void loadItems()} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No source items match these filters."
            description="Add one above to get started."
          />
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <SourceItemCard
                key={item.id}
                item={item}
                onItemChanged={handleItemChanged}
                onDeleted={handleDeleted}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
