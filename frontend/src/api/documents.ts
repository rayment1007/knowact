// Typed API wrappers for Connected Workspace Intelligence document upload,
// processing, and retrieval (M6.4, Requirement 29).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization. Responses never contain the
// document binary or its opaque storage key. Cross-org document ids yield
// ApiError(404); a document that cannot be parsed yields ApiError(422).

import { api } from "./client";
import type { DocumentAsset, Sensitivity } from "./types";

export const documentsApi = {
  /**
   * Upload a document binary (multipart). The binary is streamed into the
   * StorageBackend (never the database); the asset is persisted with status
   * UPLOADED (Requirements 29.1, 29.2).
   */
  upload: (file: File, sensitivity: Sensitivity = "INTERNAL") => {
    const form = new FormData();
    form.append("file", file);
    form.append("sensitivity", sensitivity);
    return api.upload<DocumentAsset>("/documents", form);
  },

  /** Parse → chunk → embed a document once, reaching INDEXED (Req 29.3). */
  process: (documentId: string) =>
    api.post<DocumentAsset>(`/documents/${documentId}/process`),

  /** List the organization's documents, newest first (Requirement 29.2). */
  list: () => api.get<DocumentAsset[]>("/documents"),

  /** Fetch a single document's metadata/status (Requirement 29.5). */
  get: (documentId: string) =>
    api.get<DocumentAsset>(`/documents/${documentId}`),

  /** Delete a document: binary + chunks + embeddings cascade. */
  remove: (documentId: string) =>
    api.del<void>(`/documents/${documentId}`),
};
