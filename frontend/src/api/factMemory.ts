import { api } from "./client";

export type FactRelationship = "NEW" | "SAME" | "REVISION" | "CONFLICT" | "UNCLEAR";
export type FactView = "needs_review" | "current" | "history";
export interface FactIdentity { id: string; project_id: string; fact_type: string; canonical_key: string; occurrence_key: string }
export interface FactComparison { rule_version?: string; reason_codes?: string[]; reasons?: string[]; warnings?: string[]; can_approve?: boolean; needs_resolution_reason?: boolean }
export interface FactProposal {
  id: string; identity_id: string; evidence_id: string; revision: number;
  value_json: { date: string; precision: string } | null; relationship: FactRelationship;
  comparison_ready: boolean; expected_head_id: string | null; status: string;
  result_version_id: string | null; redacted_at: string | null; created_at: string; updated_at: string;
  extraction_context: { subject?: string; occurrence?: string | null; identity_unverified?: boolean; requires_revalidation?: boolean };
  comparison_json: FactComparison;
}
export interface FactVersion {
  id: string; identity_id: string; version_number: number; value_json: { date: string; precision: string } | null;
  normalization_version: string; status: "CURRENT" | "SUPERSEDED"; predecessor_id: string | null;
  approved_by: string; approved_by_name?: string | null; approved_at: string; redacted_at: string | null;
}
export interface FactEvidence {
  id: string; excerpt: string | null; locator_json: Record<string, unknown>; redacted_at: string | null;
  source: { id: string; source_kind: "source" | "email" | "calendar" | "file"; source_id: string; source_fingerprint: string;
    source_title: string | null; source_time: string | null; captured_at: string; available: boolean; purged_at: string | null };
}
export interface FactReviewRecord {
  id: string; identity_id: string; proposal_id: string; proposal_revision: number; decision: string;
  result_version_id: string | null; detail: Record<string, unknown>; redacted_at: string | null; created_at: string; reviewer_name?: string | null;
}
export interface FactProposalDetail {
  proposal: FactProposal; identity: FactIdentity; project_name: string; current: FactVersion | null;
  evidence: FactEvidence; current_evidence: FactEvidence[]; current_evidence_total: number;
  lineage: { candidate_id: string; candidate_revision: number; resolved_proposal_id: string; active: boolean; candidate_current_revision: number; candidate_status: string } | null;
  active_resolution_id: string | null; pending_peers: FactProposal[]; pending_peers_total: number;
  can_approve: boolean; needs_resolution_reason: boolean;
}
export interface FactVersionDetail { version: FactVersion; identity: FactIdentity; project_name: string; evidence: FactEvidence[]; evidence_total: number; reviews: FactReviewRecord[]; reviews_total: number }
export interface FactPage<T> { items: T[]; total: number; limit: number; offset: number }
export interface FactIdentityOption extends FactIdentity { project_name: string; current: FactVersion | null }
export interface FactMemoryItem { id: string; record_type: "proposal" | "version"; identity: FactIdentity; project_name: string; proposal: FactProposal | null; version: FactVersion | null; title: string; value_date: string | null; relationship: FactRelationship | null; status: string; created_at: string }
export interface FactCounts { needs_review: number; unresolved: number; current: number; history: number }
export interface ResolveFactRequest { expected_revision: number; expected_source_fingerprint: string; project_id: string; identity_id: string | null; canonical_key: string | null; occurrence_key: string; request_key: string }
export interface CompareFactRequest { expected_revision: number; expected_head_id: string | null; expected_source_fingerprint: string; request_key: string }
export interface ReviewFactRequest extends CompareFactRequest { decision: "APPROVE" | "REJECT" | "KEEP_UNRESOLVED"; resolution_reason?: string | null }

export const FACT_PATH = "/dev/fact-memory";
export const factMemoryApi = {
  detail: (id: string, fresh = false) => api.get<FactProposalDetail>(`${FACT_PATH}/proposals/${id}`, fresh ? { cache: "no-store" } : undefined),
  identities: (path: string) => api.get<FactPage<FactIdentityOption>>(path),
  memory: (path: string) => api.get<FactPage<FactMemoryItem>>(path),
  counts: () => api.get<FactCounts>(`${FACT_PATH}/counts`),
  resolve: (id: string, body: ResolveFactRequest) => api.post<FactProposalDetail>(`${FACT_PATH}/proposals/${id}/resolve`, body),
  compare: (id: string, body: CompareFactRequest) => api.post<FactProposalDetail>(`${FACT_PATH}/proposals/${id}/compare`, body),
  review: (id: string, body: ReviewFactRequest) => api.post<FactReviewRecord>(`${FACT_PATH}/proposals/${id}/review`, body),
  edit: (id: string, body: { expected_revision: number; value_date: string; excerpt: string; request_key: string }) => api.patch<FactReviewRecord>(`${FACT_PATH}/proposals/${id}`, body),
  version: (id: string) => api.get<FactVersionDetail>(`${FACT_PATH}/versions/${id}`),
  versionEvidence: (path: string) => api.get<FactPage<FactEvidence>>(path),
  versionReviews: (path: string) => api.get<FactPage<FactReviewRecord>>(path),
  history: (path: string) => api.get<FactPage<FactVersion>>(path),
};
