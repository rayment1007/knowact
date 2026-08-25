// DocumentsPage: upload documents and manage their processing (Requirement 29).
//
// Responsibilities:
//   - Upload a document (PDF / DOCX / TXT / email attachments) with a chosen
//     sensitivity via `POST /api/documents` (Requirements 29.1, 29.2). The
//     binary is stored behind the backend StorageBackend, never in the DB.
//   - List uploaded documents (`GET /api/documents`) with their processing
//     status, and trigger `POST /api/documents/{id}/process` to parse → chunk →
//     embed → INDEXED (Requirement 29.3).
//   - Delete a document (`DELETE /api/documents/{id}`), cascading its chunks and
//     embeddings.
//
// Loading / empty / error states reuse the shared feedback components.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, documentsApi, getErrorMessage } from "@/api";
import type { DocumentAsset, DocumentProcessingStatus, Sensitivity } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";

const SENSITIVITIES: Sensitivity[] = [
  "PUBLIC",
  "INTERNAL",
  "CONFIDENTIAL",
  "HIGHLY_SENSITIVE",
];

const STATUS_STYLES: Record<DocumentProcessingStatus, string> = {
  UPLOADED: "bg-slate-100 text-slate-600",
  PARSING: "bg-amber-50 text-amber-700",
  CHUNKING: "bg-amber-50 text-amber-700",
  EMBEDDING: "bg-amber-50 text-amber-700",
  INDEXED: "bg-emerald-50 text-emerald-700",
  FAILED: "bg-rose-50 text-rose-700",
};

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function describeError(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return "That document no longer exists. Refreshed.";
    if ([400, 413, 415, 422].includes(err.status)) {
      return getErrorMessage(err, `Could not ${verb}.`);
    }
    return `Could not ${verb} (${err.status}). Please retry.`;
  }
  return `Could not ${verb}. Please retry.`;
}

interface UploadFormProps {
  disabled: boolean;
  onUpload: (file: File, sensitivity: Sensitivity) => void;
}

function UploadForm({ disabled, onUpload }: UploadFormProps) {
  const [sensitivity, setSensitivity] = useState<Sensitivity>("INTERNAL");
  const [file, setFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function submit() {
    if (!file) return;
    onUpload(file, sensitivity);
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Upload a document</h2>
      <p className="mt-1 text-xs text-slate-500">
        PDF, DOCX, TXT, Markdown, CSV, and EML files up to 20 MB are supported.
        Only your own uploaded documents can be retrieved.
      </p>

      <div className="mt-4 space-y-4">
        <div>
          <label
            htmlFor="document-file"
            className="text-xs font-medium text-slate-600"
          >
            File
          </label>
          <input
            id="document-file"
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown,.csv,.eml"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
          />
        </div>

        <div>
          <label
            htmlFor="document-sensitivity"
            className="text-xs font-medium text-slate-600"
          >
            Sensitivity
          </label>
          <select
            id="document-sensitivity"
            value={sensitivity}
            onChange={(event) =>
              setSensitivity(event.target.value as Sensitivity)
            }
            className="mt-1 block w-full max-w-xs rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
          >
            {SENSITIVITIES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={disabled || !file}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Upload
        </button>
      </div>
    </div>
  );
}

export default function DocumentsPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<DocumentAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openedDocument, setOpenedDocument] =
    useState<DocumentAsset | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const listRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);

  const loadOpenedDocument = useCallback(async () => {
    const requestGeneration = ++detailRequestGeneration.current;
    if (!documentId) {
      setOpenedDocument(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }

    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await documentsApi.get(documentId);
      if (requestGeneration !== detailRequestGeneration.current) return;
      setOpenedDocument(result);
    } catch (err) {
      if (requestGeneration !== detailRequestGeneration.current) return;
      setOpenedDocument(null);
      setDetailError(
        err instanceof ApiError && err.status === 404
          ? "This document could not be found."
          : "Could not load the opened document. Please retry.",
      );
    } finally {
      if (requestGeneration === detailRequestGeneration.current) {
        setDetailLoading(false);
      }
    }
  }, [documentId]);

  useEffect(() => {
    void loadOpenedDocument();
    return () => {
      detailRequestGeneration.current += 1;
    };
  }, [loadOpenedDocument]);

  const load = useCallback(async () => {
    const requestGeneration = ++listRequestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const result = await documentsApi.list();
      if (requestGeneration !== listRequestGeneration.current) return;
      setDocuments(result);
    } catch {
      if (requestGeneration !== listRequestGeneration.current) return;
      setError("Could not load your documents. Please retry.");
    } finally {
      if (requestGeneration === listRequestGeneration.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      listRequestGeneration.current += 1;
    };
  }, [load]);

  const handleUpload = useCallback(
    async (file: File, sensitivity: Sensitivity) => {
      setBusy(true);
      setActionError(null);
      try {
        await documentsApi.upload(file, sensitivity);
        await load();
      } catch (err) {
        setActionError(describeError(err, "upload the document"));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const runAction = useCallback(
    async (
      fn: () => Promise<unknown>,
      verb: string,
      deletedDocumentId?: string,
    ) => {
      setBusy(true);
      setActionError(null);
      try {
        await fn();
        await load();
        if (deletedDocumentId && deletedDocumentId === documentId) {
          setOpenedDocument(null);
          navigate("/documents", { replace: true });
        } else if (documentId) {
          await loadOpenedDocument();
        }
      } catch (err) {
        await Promise.allSettled([
          load(),
          documentId ? loadOpenedDocument() : Promise.resolve(),
        ]);
        setActionError(describeError(err, verb));
      } finally {
        setBusy(false);
      }
    },
    [documentId, load, loadOpenedDocument, navigate],
  );

  const handleDelete = useCallback(
    (document: DocumentAsset) => {
      if (
        !window.confirm(
          `Permanently delete “${document.filename}”? This removes the file, its extracted chunks, and embeddings. This cannot be undone.`,
        )
      ) {
        return;
      }
      void runAction(
        () => documentsApi.remove(document.id),
        "delete the document",
        document.id,
      );
    },
    [runAction],
  );

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <LoadingState variant="skeleton" rows={3} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">Documents</h1>
        <p className="mt-1 text-sm text-slate-500">
          Upload documents to ground answers on your files. Each document is
          parsed, chunked, and embedded once; retrieval only ever returns
          permitted, relevant content.
        </p>
      </header>

      {documentId ? (
        <section className="mb-6 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-900">
              Opened document
            </h2>
            <Link
              to="/documents"
              className="text-xs font-medium text-brand-600 hover:text-brand-700"
            >
              Close details
            </Link>
          </div>
          {detailLoading ? (
            <LoadingState label="Loading document..." />
          ) : detailError ? (
            <ErrorState
              message={detailError}
              onRetry={() => void loadOpenedDocument()}
            />
          ) : openedDocument ? (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium text-slate-900">
                    {openedDocument.filename}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {openedDocument.mime_type} · {openedDocument.sensitivity} ·{" "}
                    {formatDateTime(openedDocument.created_at)}
                  </p>
                </div>
                <span
                  className={`rounded px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[openedDocument.processing_status]}`}
                >
                  {openedDocument.processing_status}
                </span>
              </div>
              {openedDocument.failure_reason ? (
                <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">
                  {openedDocument.failure_reason}
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {["UPLOADED", "FAILED"].includes(
                  openedDocument.processing_status,
                ) ? (
                  <button
                    type="button"
                    onClick={() =>
                      void runAction(
                        () => documentsApi.process(openedDocument.id),
                        "process the document",
                      )
                    }
                    disabled={busy}
                    className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-60"
                  >
                    {openedDocument.processing_status === "FAILED"
                      ? "Retry processing"
                      : "Process"}
                  </button>
                ) : openedDocument.processing_status === "INDEXED" ? (
                  <span className="text-xs text-emerald-600">
                    Indexed and ready for retrieval.
                  </span>
                ) : (
                  <span className="text-xs text-amber-700">
                    Processing is in progress.
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => handleDelete(openedDocument)}
                  disabled={busy}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
                >
                  Delete
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <div className="space-y-6">
          {actionError ? (
            <ErrorState message={actionError} variant="alert" />
          ) : null}

          <UploadForm disabled={busy} onUpload={handleUpload} />

          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Your documents
            </h2>
            {documents.length === 0 ? (
              <EmptyState
                variant="plain"
                title="No documents yet."
                description="Upload a document above to get started."
              />
            ) : (
              <ul className="space-y-2">
                {documents.map((doc) => (
                  <li
                    key={doc.id}
                    className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link
                          to={`/documents/${doc.id}`}
                          className="block truncate text-sm font-medium text-slate-900 hover:text-brand-700"
                        >
                          {doc.filename}
                        </Link>
                        <p className="mt-0.5 truncate text-xs text-slate-500">
                          {doc.mime_type} · {doc.sensitivity} ·{" "}
                          {formatDateTime(doc.created_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span
                          className={`rounded px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[doc.processing_status]}`}
                        >
                          {doc.processing_status}
                        </span>
                      </div>
                    </div>

                    {doc.failure_reason ? (
                      <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">
                        {doc.failure_reason}
                      </p>
                    ) : null}

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {["UPLOADED", "FAILED"].includes(
                        doc.processing_status,
                      ) ? (
                        <button
                          type="button"
                          onClick={() =>
                            void runAction(
                              () => documentsApi.process(doc.id),
                              "process the document",
                            )
                          }
                          disabled={busy}
                          className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-60"
                        >
                          {doc.processing_status === "FAILED"
                            ? "Retry processing"
                            : "Process"}
                        </button>
                      ) : doc.processing_status === "INDEXED" ? (
                        <span className="text-xs text-emerald-600">
                          Indexed and ready for retrieval.
                        </span>
                      ) : (
                        <span className="text-xs text-amber-700">
                          Processing is in progress.
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDelete(doc)}
                        disabled={busy}
                        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
