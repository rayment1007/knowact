// Public entry point for the typed API layer.

export {
  api,
  apiRequest,
  ApiError,
  setUnauthorizedHandler,
} from "./client";
export type { RequestOptions } from "./client";
export { parseFieldErrors, getErrorMessage } from "./errors";
export type { FieldErrors } from "./errors";
export { authApi } from "./auth";
export type {
  Organization,
  User,
  UserRole,
  LoginRequest,
  LoginResponse,
  CurrentUserResponse,
} from "./types";

// Source Inbox / Classification / Knowledge (Requirements 3, 4, 5, 6)
export { sourceItemsApi } from "./sourceItems";
export type { InboxFilters } from "./sourceItems";
export type {
  SourceType,
  SourceStatus,
  Relevance,
  BusinessCategory,
  Sensitivity,
  SuggestionStatus,
  EvidenceSpan,
  SourceItem,
  ClassificationResult,
  SourceItemDetail,
  SourceItemCreate,
  ClassificationOverride,
  KnowledgeItem,
} from "./types";

// Knowledge Hub (Requirement 7)
export { knowledgeApi } from "./knowledge";
export type { KnowledgeFilters } from "./knowledge";

// Business entities (Requirement 10.5) — shared filter source
export { businessEntitiesApi } from "./businessEntities";

// Shared domain types reused by Knowledge Hub, Action Center, Decision Memory
export type {
  ActionStatus,
  BusinessEntityType,
  BusinessEntity,
  ActionItem,
  DecisionRecord,
  KnowledgeDetail,
} from "./types";

// Action Center (Requirement 8)
export { actionsApi } from "./actions";
export type { ActionFilters, ActionCreate, ActionUpdate } from "./actions";

// Decision Memory (Requirement 9)
export { decisionsApi } from "./decisions";
export type { DecisionFilters, DecisionCreate } from "./decisions";

// Daily Brief (Requirement 10) — Enterprise Dashboard
export { briefApi } from "./brief";
// Shared human-readable evidence resolution for brief lines (Requirement 10.4)
export { readableEvidence } from "./evidence";
export type {
  BriefLine,
  ActionSuggestion,
  FollowUpSuggestion,
  DailyBriefContent,
  BriefResponse,
} from "./types";

// Connected Workspace Intelligence — Integrations & Google Sign-In (Req 23-25)
export { integrationsApi, googleAuthApi } from "./integrations";
export type {
  IntegrationProvider,
  IntegrationService,
  ConnectionStatus,
  IntegrationConnection,
  AuthorizationRedirect,
} from "./types";

// Connected Workspace Intelligence — Gmail sync & ingestion (Req 26, 27)
export { gmailApi } from "./gmail";
export type {
  AttachmentHandling,
  StoragePolicy,
  SenderSignalType,
  InitialSyncOptions,
  SyncRun,
  EmailMessageRecord,
  EmailTaskSuggestion,
  EmailTaskSuggestionEdit,
  SenderSignalRequest,
  SenderSignal,
} from "./types";

// Connected Workspace Intelligence — Calendar from confirmed actions (Req 28)
export { calendarApi } from "./calendar";
export type {
  CalendarSyncStatus,
  CalendarView,
  CalendarAddRequest,
  CalendarUpdateRequest,
  CalendarDailyBriefBlockRequest,
  CalendarEventLink,
} from "./types";

// Connected Workspace Intelligence — Documents & retrieval (Req 29)
export { documentsApi } from "./documents";
export type { DocumentProcessingStatus, DocumentAsset } from "./types";

// Connected Workspace Intelligence — Enterprise Copilot (Req 30, 31)
export { copilotApi } from "./copilot";
export type {
  CopilotIntent,
  CopilotAskRequest,
  Citation,
  SuggestedArtifact,
  CopilotResponse,
  SuggestedQuestionsResponse,
  CopilotConfirmRequest,
  CopilotConfirmResponse,
} from "./types";

// Connected Workspace Intelligence — Gmail AI Draft (Req 32)
export { emailDraftsApi } from "./emailDrafts";
export type {
  EmailDraftStatus,
  ReferencedFact,
  EmailDraft,
  EmailDraftRequest,
  EmailDraftEdit,
  EmailDraftSendRequest,
} from "./types";

// Connected Workspace Intelligence — Privacy, control & deletion (Req 33)
export { privacyApi } from "./privacy";
export type {
  RawEmailRetentionMode,
  EmailDataDeleteScope,
  EmailDataDeleteRequest,
  EmailDataDeleteResponse,
  RetentionPolicyUpdate,
  RetentionPolicy,
  SyncStatus,
  AnswerCitation,
  AnswerProvenance,
} from "./types";
