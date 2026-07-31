// Shared API types for the KnowAct frontend.
//
// These types are defined against the documented backend API contract (see
// design.md). The backend auth routes/schemas may not exist yet; these types
// describe the shapes the frontend expects and will be kept in sync with the
// Pydantic response schemas.

/** A user role within an organization. */
export type UserRole = "ADMIN" | "ADVISOR" | "MEMBER";

/**
 * An organization (tenant). Every domain row is scoped to an organization.
 */
export interface Organization {
  id: string;
  name: string;
  created_at: string;
}

/**
 * The authenticated user. The backend NEVER returns the password hash, so it
 * is intentionally absent from this type.
 */
export interface User {
  id: string;
  organization_id: string;
  email: string;
  full_name: string;
  role: UserRole;
  created_at: string;
}

/**
 * Response body of `POST /api/auth/login`. Authentication is cookie-based: the
 * signed JWT is set as an HTTP-only cookie on the response and is never exposed
 * to client JavaScript, so the body carries only the authenticated user.
 */
export interface LoginResponse {
  user: User;
}

/**
 * Response body of `GET /api/auth/me`: the current user together with their
 * organization. Excludes the password hash.
 */
export interface CurrentUserResponse {
  user: User;
  organization: Organization;
}

/** Request body of `POST /api/auth/login`. */
export interface LoginRequest {
  email: string;
  password: string;
}

// ===========================================================================
// Source Inbox / Classification / Knowledge (Requirements 3, 4, 5, 6)
// ---------------------------------------------------------------------------
// Types below mirror the Core Engine Pydantic schemas (see backend
// app/core/schemas.py). They are additive: later page tasks extend this file
// with their own well-sectioned blocks. Enums are modeled as string-literal
// unions so they serialize exactly as the backend enum values.
// ===========================================================================

/** The kind of raw content a source item was collected from. */
export type SourceType =
  | "EMAIL"
  | "DOCUMENT"
  | "MEETING_NOTE"
  | "TASK_NOTE"
  | "CHAT"
  | "MANUAL";

/** Lifecycle status of a source item as it moves through the pipeline. */
export type SourceStatus =
  | "NEW"
  | "CLASSIFIED"
  | "PROCESSED"
  | "ARCHIVED"
  | "DISMISSED";

/** Classification axis 1: is this work content or noise? */
export type Relevance =
  | "WORK_RELATED"
  | "PERSONAL"
  | "IRRELEVANT"
  | "SPAM"
  | "SYSTEM_NOTIFICATION";

/** Classification axis 2: what kind of business content is this? */
export type BusinessCategory =
  | "CLIENT"
  | "PROJECT"
  | "DECISION"
  | "TASK"
  | "RISK"
  | "MEETING"
  | "PARTNER"
  | "LEARNING"
  | "OTHER";

/** Classification axis 3: how protected is this content? */
export type Sensitivity =
  | "PUBLIC"
  | "INTERNAL"
  | "CONFIDENTIAL"
  | "HIGHLY_SENSITIVE";

/** Human-in-the-loop status shared by AI-produced suggestions. */
export type SuggestionStatus = "SUGGESTED" | "CONFIRMED" | "REJECTED";

/**
 * A span of source text supporting a classification. Character offsets are
 * optional because the mock provider may not always populate them.
 */
export interface EvidenceSpan {
  text: string;
  start?: number;
  end?: number;
}

/** A collected unit of raw organizational content (Requirement 3.1). */
export interface SourceItem {
  id: string;
  organization_id: string;
  created_by: string;
  source_type: SourceType;
  title: string;
  content: string;
  status: SourceStatus;
  received_at: string;
  created_at: string;
}

/**
 * An AI classification suggestion for a source item (Requirement 4). Produced
 * with status SUGGESTED and only mutated by explicit human confirm/reject.
 */
export interface ClassificationResult {
  id: string;
  source_item_id: string;
  relevance: Relevance;
  business_category: BusinessCategory;
  sensitivity: Sensitivity;
  confidence: number;
  reasons: string[];
  evidence_spans: EvidenceSpan[];
  status: SuggestionStatus;
  created_at: string;
}

/** Response body of `GET /api/source-items/{id}` (Requirement 3.3). */
export interface SourceItemDetail {
  source_item: SourceItem;
  classification: ClassificationResult | null;
}

/** Request body of `POST /api/source-items` (Requirement 3.1). */
export interface SourceItemCreate {
  source_type: SourceType;
  title: string;
  content: string;
  received_at?: string;
}

/**
 * Optional human edits applied when confirming a classification
 * (Requirement 5.2). Any omitted axis keeps the AI-suggested value.
 */
export interface ClassificationOverride {
  relevance?: Relevance;
  business_category?: BusinessCategory;
  sensitivity?: Sensitivity;
}

/**
 * A structured, evidence-backed piece of knowledge extracted from a source
 * item (Requirement 6). Produced with status SUGGESTED.
 */
export interface KnowledgeItem {
  id: string;
  organization_id: string;
  business_entity_id: string | null;
  source_item_id: string | null;
  summary: string;
  key_points: string[];
  evidence_text: string;
  knowledge_type: string;
  status: SuggestionStatus;
  created_at: string;
  updated_at: string;
}

// ===========================================================================
// Shared domain types: Business Entities, Actions, Decisions (Requirements 7,
// 8, 9, 10)
// ---------------------------------------------------------------------------
// These types mirror the Core Engine Pydantic schemas (see backend
// app/core/schemas.py) and are SHARED across pages. The Knowledge Hub (task
// 13.2) is the first consumer — it lists business entities for its filter and
// surfaces the actions/decisions linked to a knowledge item — but the Action
// Center (13.3) and Decision Memory (13.4) tasks REUSE these same types rather
// than redefining them. Enums are string-literal unions so they serialize
// exactly as the backend enum values.
// ===========================================================================

/** Lifecycle status of an action item (mirrors backend ActionStatus). */
export type ActionStatus = "OPEN" | "IN_PROGRESS" | "DONE" | "CANCELLED";

/** The kind of domain object a business entity represents. */
export type BusinessEntityType =
  | "CLIENT"
  | "PROJECT"
  | "DEPARTMENT"
  | "VENDOR"
  | "PARTNER"
  | "ACCOUNT"
  | "PROCESS";

/**
 * A domain object (client, project, vendor, department, …) that accumulates
 * knowledge, actions, and decisions and can be the subject of a brief
 * (Requirement 10.5). Used by the Knowledge Hub as the entity filter source.
 */
export interface BusinessEntity {
  id: string;
  organization_id: string;
  entity_type: BusinessEntityType;
  name: string;
  description: string | null;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/**
 * An action item (Requirement 8). Surfaced in a knowledge item's detail as one
 * of its linked actions (Requirement 7.4).
 */
export interface ActionItem {
  id: string;
  organization_id: string;
  business_entity_id: string | null;
  knowledge_item_id: string | null;
  source_meeting_id: string | null;
  title: string;
  description: string | null;
  owner_id: string | null;
  due_date: string | null;
  status: ActionStatus;
  evidence_text: string | null;
  ai_generated: boolean;
  created_at: string;
}

/**
 * A decision record (Requirement 9): an immutable record of a decision already
 * made, with its rationale and supporting evidence. Surfaced in a knowledge
 * item's detail as one of its linked decisions (Requirement 7.4).
 */
export interface DecisionRecord {
  id: string;
  organization_id: string;
  business_entity_id: string | null;
  title: string;
  decision: string;
  rationale: string;
  evidence_text: string;
  decided_by: string;
  decided_at: string;
}

/**
 * Response body of `GET /api/knowledge/{id}`: a knowledge item together with
 * the actions and decisions linked to it (Requirements 7.4, 7.5).
 */
export interface KnowledgeDetail {
  knowledge_item: KnowledgeItem;
  linked_actions: ActionItem[];
  linked_decisions: DecisionRecord[];
}

// ===========================================================================
// Daily Brief (Requirement 10)
// ---------------------------------------------------------------------------
// Types below mirror the Core Engine brief schemas. `BriefResponse` mirrors
// the backend `BriefResponse` (see backend app/core/schemas.py); its `content`
// is the serialized `DailyBriefOutput` (see
// app/core/services/ai_provider.py). The `content` is typed loosely as a dict
// server-side because its shape depends on the brief scope — for a DAILY brief
// it is `DailyBriefContent`. Consumed by the Enterprise Dashboard (task 13.4).
// ===========================================================================

/**
 * One evidence-backed line of a brief (a priority or a risk). `evidence_ref`
 * points back at the supporting evidence (an opaque source/knowledge id, kept
 * for traceability); `evidence_text` is the resolved, human-readable snippet
 * for that reference (populated by the backend) that the UI should display;
 * `entity_ref` links the line to the business entity it concerns
 * (Requirement 10.4).
 */
export interface BriefLine {
  text: string;
  evidence_ref?: string | null;
  evidence_text?: string | null;
  entity_ref?: string | null;
}

/**
 * A proposed action item surfaced in the brief's recommended actions.
 * `owner_hint` is a free-text hint (e.g. a name), `due_hint` a suggested due
 * date; `evidence_text` keeps the action traceable to its source and is
 * always present (Requirement 10.4).
 */
export interface ActionSuggestion {
  title: string;
  description?: string | null;
  owner_hint?: string | null;
  due_hint?: string | null;
  evidence_text: string;
}

/**
 * A proposed follow-up surfaced in the daily brief — lighter-weight than an
 * `ActionSuggestion`. Optionally tied to an entity, a suggested date, and its
 * supporting evidence.
 */
export interface FollowUpSuggestion {
  text: string;
  entity_ref?: string | null;
  due_hint?: string | null;
  evidence_text?: string | null;
}

/**
 * The serialized `DailyBriefOutput` carried in a DAILY `BriefResponse.content`:
 * a headline plus the four narrative sections — priorities, recommended
 * actions, follow-ups, and risks — each grounded in confirmed context
 * (Requirements 10.1, 10.3, 10.4).
 */
export interface DailyBriefContent {
  headline: string;
  priorities: BriefLine[];
  recommended_actions: ActionSuggestion[];
  follow_ups: FollowUpSuggestion[];
  risks: BriefLine[];
}

/**
 * Response body of `GET /api/brief/daily` (Requirements 10.1, 10.3). Mirrors
 * the backend `Brief`. For a DAILY brief, `scope` is `"DAILY"` and `content`
 * is a `DailyBriefContent`.
 */
export interface BriefResponse {
  id: string;
  organization_id: string;
  scope: "DAILY" | "ENTITY" | string;
  scope_ref_id: string | null;
  generated_for: string;
  content: DailyBriefContent;
  created_at: string;
}

// ===========================================================================
// Connected Workspace Intelligence — Integrations (Requirements 23, 24, 25)
// ---------------------------------------------------------------------------
// These types mirror the token-SAFE CWI response schemas. The backend NEVER
// serializes token/secret columns, so no encrypted-token or key field exists
// on any type here (Requirements 25.2, 25.3).

/** The external provider a connection belongs to. */
export type IntegrationProvider = "GOOGLE";

/** The specific Google service a connection authorizes. */
export type IntegrationService = "GMAIL" | "GOOGLE_CALENDAR";

/** Lifecycle status of an integration connection. */
export type ConnectionStatus = "CONNECTED" | "EXPIRED" | "REVOKED" | "ERROR";

/**
 * A token-safe view of an integration connection (mirrors
 * `IntegrationConnectionView`). Exposes only status, account email, granted
 * scopes, last sync time, and last error — never a token or key.
 */
export interface IntegrationConnection {
  id: string;
  organization_id: string;
  user_id: string;
  provider: IntegrationProvider;
  service: IntegrationService;
  account_email: string;
  status: ConnectionStatus;
  granted_scopes: string[];
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * A redirect to follow to begin a Google authorization flow (sign-in or
 * incremental service authorization). Carries only a URL and opaque state.
 */
export interface AuthorizationRedirect {
  authorization_url: string;
  state: string;
}

// ===========================================================================
// Connected Workspace Intelligence — Gmail sync & ingestion (Req 26, 27)
// ---------------------------------------------------------------------------

/** Attachment handling choice for an initial Gmail import (Requirement 26.4). */
export type AttachmentHandling = "IGNORE" | "METADATA_ONLY" | "STORE";

/** Raw-content retention policy for an initial Gmail import (Requirement 26.5). */
export type StoragePolicy = "RAW_AND_EXTRACTED" | "EXTRACTED_ONLY";

/** Negative-signal kind attached to a sender/domain (Requirement 27.7). */
export type SenderSignalType = "PERSONAL" | "IRRELEVANT";

/** Options controlling the first Gmail import (Requirements 26.1-26.5). */
export interface InitialSyncOptions {
  date_range_days: 7 | 30 | 90;
  labels: string[] | null;
  include_sent: boolean;
  attachment_handling: AttachmentHandling;
  storage_policy: StoragePolicy;
}

/** A token-safe summary of a completed sync pass. */
export interface SyncRun {
  messages_seen: number;
  records_created: number;
  source_items_created: number;
  skipped_duplicates: number;
  skipped_ineligible: number;
  suggestions_created: number;
  record_ids: string[];
}

/** Provenance-only view of an ingested Gmail message (Requirement 27.1). */
export interface EmailMessageRecord {
  id: string;
  organization_id: string;
  integration_connection_id: string;
  source_item_id: string | null;
  gmail_message_id: string;
  gmail_thread_id: string;
  sender: string;
  recipients: string[];
  subject: string;
  received_at: string;
  labels: string[];
  content_hash: string;
  has_attachments: boolean;
  stored_raw: boolean;
  created_at: string;
}

/** An AI-extracted task suggestion awaiting human review (Requirement 27.6). */
export interface EmailTaskSuggestion {
  id: string;
  organization_id: string;
  email_message_record_id: string;
  source_item_id: string | null;
  business_entity_id: string | null;
  title: string;
  description: string | null;
  suggested_due_date: string | null;
  suggested_owner: string | null;
  related_entity_name: string | null;
  gmail_message_id: string;
  evidence_text: string;
  ai_provider: string;
  ai_model: string;
  status: SuggestionStatus;
  created_at: string;
  updated_at: string;
}

/** Editable fields when a user edits a task suggestion (Requirement 27.7). */
export interface EmailTaskSuggestionEdit {
  title?: string;
  description?: string | null;
  suggested_due_date?: string | null;
  suggested_owner?: string | null;
  related_entity_name?: string | null;
  business_entity_id?: string | null;
}

/** Request to mark a sender/domain as a negative signal (Requirement 27.7). */
export interface SenderSignalRequest {
  pattern: string;
  signal_type: SenderSignalType;
}

/** A persisted sender/domain negative signal. */
export interface SenderSignal {
  id: string;
  organization_id: string;
  integration_connection_id: string | null;
  pattern: string;
  signal_type: SenderSignalType;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Calendar events from confirmed actions (M6.3, Requirement 28)
// ---------------------------------------------------------------------------

/** Sync lifecycle of a calendar event link (mirrors `CalendarSyncStatus`). */
export type CalendarSyncStatus =
  | "PENDING"
  | "SYNCED"
  | "UPDATE_PENDING"
  | "CANCEL_PENDING"
  | "CANCELLED"
  | "FAILED";

/** A single writable Google Calendar the user can add events to (Req 28.3). */
export interface CalendarView {
  calendar_id: string;
  summary: string;
  primary: boolean;
  access_role: string;
}

/** The explicit "Add to Google Calendar" request for a confirmed action. */
export interface CalendarAddRequest {
  connection_id: string;
  google_calendar_id: string;
  summary?: string | null;
  description?: string | null;
  start?: string | null;
  end?: string | null;
  all_day?: boolean;
  all_day_date?: string | null;
  reminder_minutes?: number[];
}

/** Editable fields applied when updating an existing calendar event (28.6). */
export interface CalendarUpdateRequest {
  summary?: string | null;
  description?: string | null;
  start?: string | null;
  end?: string | null;
  all_day?: boolean;
  all_day_date?: string | null;
  reminder_minutes?: number[];
}

/** Request to create the optional recurring Daily Brief block (Req 28.9). */
export interface CalendarDailyBriefBlockRequest {
  connection_id: string;
  google_calendar_id: string;
  deep_link: string;
  label?: string;
  start?: string | null;
  recurrence?: string;
}

/**
 * A view of a calendar event link with its sync state (Requirements 28.5,
 * 28.6). The `ActionItem` remains the source of truth; this row records the
 * externally-created Google event and how the sync is progressing.
 */
export interface CalendarEventLink {
  id: string;
  organization_id: string;
  user_id: string;
  action_item_id: string | null;
  integration_connection_id: string;
  google_calendar_id: string;
  google_event_id: string | null;
  idempotency_key: string;
  sync_status: CalendarSyncStatus;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

// ===========================================================================
// Connected Workspace Intelligence — Documents & retrieval (Requirement 29)
// ---------------------------------------------------------------------------

/** The processing lifecycle of an uploaded document. */
export type DocumentProcessingStatus =
  | "UPLOADED"
  | "PARSING"
  | "CHUNKING"
  | "EMBEDDING"
  | "INDEXED"
  | "FAILED";

/**
 * An uploaded document's metadata + processing status. The binary itself lives
 * behind the StorageBackend and is never exposed; the opaque storage key is
 * intentionally absent from this view.
 */
export interface DocumentAsset {
  id: string;
  organization_id: string;
  uploaded_by: string;
  filename: string;
  mime_type: string;
  checksum: string;
  processing_status: DocumentProcessingStatus;
  sensitivity: Sensitivity;
  source_deleted: boolean;
  created_at: string;
}

// ===========================================================================
// Enterprise Copilot — grounded chat (M6.5, Requirements 30, 31)
// ---------------------------------------------------------------------------
// Types below mirror the Copilot Pydantic schemas (see backend
// app/modules/cwi/schemas.py). The Copilot is a grounded reader: it answers
// ONLY from confirmed, permitted data and returns citations that are validated
// against the bounded evidence set (no fabricated citations). DRAFT/ACT results
// are SUGGESTED artifacts requiring an explicit confirm before any mutation.
// ===========================================================================

/** The capability tier a Copilot request resolves to. */
export type CopilotIntent = "ASK" | "DRAFT" | "ACT";

/** Request body of `POST /api/copilot/ask`. */
export interface CopilotAskRequest {
  question: string;
  intent?: CopilotIntent;
  business_entity_id?: string;
}

/**
 * A grounded citation (design *Citation Contract*). Every field is populated by
 * the backend from a record that was actually in the bounded evidence set, so a
 * citation can never be fabricated (Requirement 30.4, 30.7).
 */
export interface Citation {
  source_type: string;
  source_id: string;
  title: string;
  evidence_excerpt: string;
  timestamp: string | null;
  deep_link: string;
}

/**
 * A DRAFT/ACT artifact awaiting explicit confirmation (Requirement 30.3). Its
 * `status` is always `SUGGESTED` on return from ask — the Copilot never applies
 * it; confirming is a separate, explicit step.
 */
export interface SuggestedArtifact {
  status: "SUGGESTED";
  tier: "DRAFT" | "ACT";
  kind: string;
  title: string | null;
  body: string | null;
  details: Record<string, unknown>;
}

/**
 * Response body of `POST /api/copilot/ask`. Either an `answer` + `citations`
 * (ASK) or a `suggested_artifact` (DRAFT/ACT). `insufficient_evidence` is set
 * when the Copilot cannot find enough confirmed evidence to answer.
 */
export interface CopilotResponse {
  answer: string | null;
  citations: Citation[];
  insufficient_evidence: boolean;
  suggested_artifact: SuggestedArtifact | null;
  intent: string;
  /** Id of the persisted answer log; used to fetch "what data was used". */
  answer_id: string | null;
}

/**
 * Response body of `GET /api/copilot/suggested-questions` (Requirement 30.6): a
 * small fixed set plus a dynamic set derived from confirmed, org-scoped context.
 */
export interface SuggestedQuestionsResponse {
  fixed: string[];
  dynamic: string[];
  questions: string[];
}

/**
 * Request body of `POST /api/copilot/confirm` (confirm-before-mutate). Echoes
 * the previously-SUGGESTED artifact the user approved. Only after this explicit
 * step does any mutation occur, writing exactly one audit row (Req 31.3, 31.4).
 */
export interface CopilotConfirmRequest {
  kind: string;
  title?: string;
  body?: string;
  business_entity_id?: string;
  due_date?: string;
  evidence_text?: string;
}

/** Response body of `POST /api/copilot/confirm`. */
export interface CopilotConfirmResponse {
  applied: boolean;
  kind: string;
  created_id: string | null;
  message: string;
}

// ---------------------------------------------------------------------------
// Gmail AI Draft (M6.6, Requirement 32)
// ---------------------------------------------------------------------------

/** The full lifecycle status of an AI-assisted Gmail draft (Requirement 32.10). */
export type EmailDraftStatus =
  | "AI_SUGGESTED"
  | "USER_APPROVED"
  | "GMAIL_DRAFT_CREATED"
  | "SENDING"
  | "SENT"
  | "REJECTED"
  | "FAILED";

/**
 * A grounded fact the draft leans on (Requirement 32.1). Each `source_id`
 * corresponds to a record the backend actually supplied to the provider; the
 * backend drops any fabricated id before persisting.
 */
export interface ReferencedFact {
  source_type: string;
  source_id: string;
  evidence: string;
  timestamp: string | null;
}

/**
 * A token-safe projection of an AI-assisted Gmail draft. Recipients are always
 * resolved and validated on the backend (Requirement 32.2); the AI never
 * contributes an address. No token or secret is ever present.
 */
export interface EmailDraft {
  id: string;
  organization_id: string;
  user_id: string;
  integration_connection_id: string;
  source_item_id: string | null;
  business_entity_id: string | null;
  gmail_thread_id: string | null;
  to_recipients: string[];
  subject: string;
  body_text: string;
  tone: string;
  purpose: string;
  referenced_facts: ReferencedFact[];
  warnings: string[];
  status: EmailDraftStatus;
  gmail_draft_id: string | null;
  gmail_sent_message_id: string | null;
  created_at: string;
  updated_at: string;
}

/** Request body of `POST /api/email-drafts` (Requirement 32.4). */
export interface EmailDraftRequest {
  connection_id: string;
  source_item_id?: string | null;
  business_entity_id?: string | null;
  to_recipients?: string[];
  purpose?: string;
  tone?: string;
}

/** Editable fields applied when a user edits a draft (Requirement 32.5). */
export interface EmailDraftEdit {
  subject?: string;
  body_text?: string;
  tone?: string;
  purpose?: string;
  to_recipients?: string[];
}

/**
 * Request body of `POST /api/email-drafts/{id}/send` (Requirement 32.7). Sending
 * requires a separate explicit confirmation distinct from approving content:
 * `confirm` must be `true` or the send is rejected and no email is sent.
 */
export interface EmailDraftSendRequest {
  confirm: boolean;
}

// ===========================================================================
// Privacy, control & deletion (M6.7, Requirement 33)
// ---------------------------------------------------------------------------
// Types below mirror the privacy Pydantic schemas (see backend
// app/modules/cwi/schemas.py). They power the Privacy / data-control page:
// disconnect/revoke, delete imported email data, delete uploaded documents,
// choose the raw-email retention policy, view last-sync status, and see exactly
// what data grounded an AI answer. No token or secret is ever part of a view.
// ===========================================================================

/** The raw-email retention mode a user chooses (Requirement 33.5). */
export type RawEmailRetentionMode = "RAW_AND_EXTRACTED" | "EXTRACTED_ONLY";

/** The scope of an email-data deletion (Requirement 33.3). */
export type EmailDataDeleteScope = "ALL" | "CONNECTION" | "SELECTED";

/** Request body of `DELETE /api/privacy/email-data` (Requirement 33.3). */
export interface EmailDataDeleteRequest {
  scope: EmailDataDeleteScope;
  connection_id?: string | null;
  record_ids?: string[];
}

/** Summary of an email-data deletion cascade (Requirements 33.3, 33.8). */
export interface EmailDataDeleteResponse {
  deleted_records: number;
  deleted_task_suggestions: number;
  deleted_chunks: number;
  marked_documents_source_deleted: number;
  retained_derived_records: number;
}

/** Request body of `PUT /api/privacy/retention-policy` (Requirement 33.5). */
export interface RetentionPolicyUpdate {
  mode: RawEmailRetentionMode;
  retention_window_days?: number | null;
}

/** A view of the persisted raw-email retention policy (Requirement 33.5). */
export interface RetentionPolicy {
  id: string;
  organization_id: string;
  user_id: string;
  mode: RawEmailRetentionMode;
  retention_window_days: number | null;
  created_at: string;
  updated_at: string;
}

/** A connection's last-sync status surfaced by the Privacy page (33.6). */
export interface SyncStatus {
  connection_id: string;
  service: IntegrationService;
  account_email: string;
  status: ConnectionStatus;
  last_sync_at: string | null;
  last_error: string | null;
}

/** One citation/evidence item recorded for a Copilot answer (33.7). */
export interface AnswerCitation {
  source_type: string;
  source_id: string;
  title: string;
  evidence_excerpt: string;
  timestamp: string | null;
  deep_link: string;
}

/** The exact data that grounded a Copilot answer (Requirement 33.7). */
export interface AnswerProvenance {
  answer_id: string;
  question: string;
  answer: string | null;
  intent: string;
  insufficient_evidence: boolean;
  citations: AnswerCitation[];
  evidence: AnswerCitation[];
  created_at: string;
}
