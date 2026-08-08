# Implementation Plan

This plan implements the requirements in dependency order. Every phase ends with a working,
testable state and should be committed separately.

## Phase 0: Baseline and Shared Contracts

- [ ] 0.1 Record the current frontend build and targeted backend test baseline.
- [x] 0.2 Add frontend unit-test tooling and a `test` script without changing production behavior.
- [ ] 0.3 Add shared navigation types and shared backend/frontend page-envelope types behind no-op
      compatibility helpers.
- [ ] 0.4 Keep unrelated untracked files out of feature commits.

**Gate:** existing frontend build passes; current targeted backend tests pass.

## Phase 1: Truly Consolidate the Workspace Interface

Requirements: 1, 2, 16.6, 16.8.

- [x] 1.1 Replace the current nested sidebar implementation with one `NAV_GROUPS` definition that
      renders exactly Dashboard, Sources, Knowledge, Actions, Copilot, and Settings.
- [x] 1.2 Move Gmail Drafts from Actions to Copilot and reserve Verification under Knowledge.
- [x] 1.3 Make every group click navigate to its first page.
- [x] 1.4 Add a shared context-tab bar in the main content area:
      Sources = Inbox/Gmail/Files; Knowledge = Hub/Decisions/Verification;
      Copilot = Assistant/Email Drafts; Settings = Connections/Privacy.
- [x] 1.5 Add a mobile navigation drawer below 768 px and horizontally scrollable context tabs.
- [x] 1.6 Preserve all eleven existing paths.
- [x] 1.7 Add dynamic routes for knowledge, action, decision, email, calendar, and document detail.
- [x] 1.8 Add missing org-scoped single-record backend endpoints and frontend wrappers.
- [x] 1.9 Make existing pages select a detail record from route parameters and display an in-shell
      not-found state.
- [x] 1.10 Read `business_entity_id` from the Knowledge URL and use router links for citations.
- [x] 1.11 Add route/navigation tests for all legacy paths, deep links, active groups, default group
      navigation, and mobile reachability.

**Gate:** all legacy and detail URLs resolve; the sidebar has only six visible purposes; related
pages are switched through context tabs rather than a long sidebar list.

**Verified 2026-08-08:** frontend typecheck and production build pass; 41 frontend tests and 51
related backend route tests pass.

**Suggested commit:** `feat(workspace): consolidate navigation and preserve deep links`

## Phase 2: Fix Upload Safety Before Adding Media

Requirements: 3, 13, 16.5; properties P22 and P23.

- [ ] 2.1 Add `failure_reason` to `DocumentAsset` through an Alembic migration, model, schema, and
      frontend type.
- [ ] 2.2 Implement `UploadValidator` with the closed MIME/extension/signature allow-list.
- [ ] 2.3 Enforce zero-byte and family-specific size limits before storage or database writes.
- [ ] 2.4 Store and process the validated effective MIME type.
- [ ] 2.5 Replace substring parser dispatch with an exact validated-type registry.
- [ ] 2.6 Remove `latin-1` and replacement decoding; fail undecodable text explicitly.
- [ ] 2.7 Ensure failed parsing produces zero chunks and persists `FAILED` plus a readable reason
      without being rolled back by the HTTP error path.
- [ ] 2.8 Add regression and property tests for unsupported, oversized, empty, renamed, mismatched,
      and undecodable payloads and for orphan-free rejection.

**Gate:** unsafe input can never create chunks or searchable text; every failure is visible and
retriable when appropriate.

**Suggested commit:** `fix(documents): validate uploads and prevent mojibake indexing`

## Phase 3: Introduce Pagination End to End

Requirements: 11, 12, 13, 16; properties P24 and P25.

- [ ] 3.1 Add shared backend `PageParams` and generic `PageEnvelope` schemas.
- [ ] 3.2 Add a pagination query helper that counts after all organization/filter/security
      predicates and applies stable ordering with an ID tie-break.
- [ ] 3.3 Migrate Core lists: source items, knowledge, actions, decisions, business entities, and
      audit logs.
- [ ] 3.4 Migrate Connected Workspace lists: integrations, Gmail messages, Gmail suggestions,
      calendar links, documents, email drafts, Copilot history, and privacy sync status.
- [ ] 3.5 Update every frontend API wrapper from raw arrays to `PageEnvelope<T>`.
- [ ] 3.6 Add shared `PaginationControls` and `usePaginatedList`.
- [ ] 3.7 Update every list page, including independent pagination state for multiple lists on one
      page.
- [ ] 3.8 Reset pagination on filter changes and reload/step back after mutations.
- [ ] 3.9 Add partition, bounds, deterministic-order, and tenant-isolation tests.

**Gate:** no org-scoped list returns an unbounded array; every affected screen can move between
pages and displays position/total.

**Suggested commits:** one backend/frontend vertical slice per domain, ending with
`feat(pagination): complete paginated workspace lists`.

## Phase 4: Build the Media Extraction Backend

Requirements: 4, 5, 7, 8, 9, 13, 14, 15; properties P21, P26-P28, P30, P31.

- [ ] 4.1 Add `AWAITING_REVIEW`, `original_binary_available`, `ExtractedTextSuggestion`, extraction
      method, and `MediaRetentionPolicy` through one reviewed migration.
- [ ] 4.2 Add Pydantic request/response schemas that never expose original bytes or credentials.
- [ ] 4.3 Add OCR and ASR protocols, deterministic mocks, real-provider adapters, configuration,
      dependency wiring, timeout handling, and development fallback logging.
- [ ] 4.4 Dispatch validated image uploads to OCR and audio uploads to ASR.
- [ ] 4.5 Implement the idempotent UPLOADED -> PARSING -> AWAITING_REVIEW lifecycle.
- [ ] 4.6 Prevent a second provider call while an asset is parsing or already has a suggestion.
- [ ] 4.7 Implement get/edit/confirm/reject/retry endpoints for extracted text.
- [ ] 4.8 On confirm, create one Source Item, chunk/embed the confirmed text, and reach INDEXED.
- [ ] 4.9 On reject, retain zero derived chunks.
- [ ] 4.10 Persist audit, reviewer, timestamps, confidence, method, language, and original machine
      text correctly and idempotently.
- [ ] 4.11 Implement original-binary availability behavior and full media deletion.
- [ ] 4.12 Implement GET/PUT media-retention policy and discard only after successful extraction.
- [ ] 4.13 Add route, service, provider, isolation, idempotency, timeout, binary-loss, retention, and
      property tests.

**Gate:** image/audio processing always stops at unverified text; only confirmation creates
searchable chunks; all tests use mock providers.

**Suggested commit:** `feat(media): add human-verified OCR and transcription lifecycle`

## Phase 5: Add Media Review to the Files Interface

Requirements: 4.11, 7.12, 9.5-9.6, 12, 14, 15.3.

- [ ] 5.1 Extend Files upload controls and copy for the supported text, image, and audio types.
- [ ] 5.2 Automatically call process after a successful upload.
- [ ] 5.3 Display UPLOADED, PARSING, AWAITING_REVIEW, INDEXED, and FAILED states clearly.
- [ ] 5.4 Show failure reason and Retry only when the original binary is available.
- [ ] 5.5 Add the extracted-text review editor with machine-extracted/unverified label, method,
      confidence, Confirm, and Reject.
- [ ] 5.6 Keep extracted text visible and disable original-dependent controls when the binary is
      unavailable.
- [ ] 5.7 Add Original images & recordings retention controls to Privacy & Data.
- [ ] 5.8 Add frontend tests for status, review, retry, unavailable-original, and retention flows.

**Gate:** a user can upload, follow progress, review/edit, confirm/reject, and understand every
failure state without leaving the Files tab.

**Suggested commit:** `feat(files): add media extraction review interface`

## Phase 6: Add Microphone Capture to Source Inbox

Requirements: 5, 6, 7, 14.

- [ ] 6.1 Replace the separate note form with a Capture Composer containing Write a note and Record
      voice modes.
- [ ] 6.2 Implement the MediaRecorder state machine for capability, permission, missing device,
      recording, elapsed time, stop, discard, and 300-second auto-stop.
- [ ] 6.3 Reject recordings below one second with clear feedback.
- [ ] 6.4 Upload valid recordings through the same audio endpoint and invoke processing
      automatically.
- [ ] 6.5 Retain a failed-upload Blob in the browser and provide Retry and Discard.
- [ ] 6.6 Reuse the Files review surface/deep link for the transcription suggestion.
- [ ] 6.7 Add mocked browser-media unit tests and a manual permission-denied check.

**Gate:** voice capture produces one retriable audio asset and one unverified transcription using
the same backend path as uploaded audio.

**Suggested commit:** `feat(inbox): add microphone capture and transcription`

## Phase 7: Add the Unified Verification Queue

Requirements: 1.12, 10, 11, 12, 13; property P29.

- [ ] 7.1 Add normalized queue entry/status/artifact-type schemas.
- [ ] 7.2 Implement organization-scoped Needs Review and History queries, pagination, filters, total,
      and per-type counts.
- [ ] 7.3 Include classifications, knowledge, actions, decisions, email drafts, extracted text, and
      pending Gmail action suggestions using the normalization in `design.md`.
- [ ] 7.4 Return one valid application path for every queue entry.
- [ ] 7.5 Add `/verification` under Knowledge with Needs review and History tabs and artifact filters.
- [ ] 7.6 Add the pending count badge to the Knowledge/Verification navigation context.
- [ ] 7.7 Navigate Review actions to existing review pages; do not duplicate confirmation logic.
- [ ] 7.8 Add queue partition, count, ordering, transition, 401, 404, and tenant-isolation tests.

**Gate:** every pending artifact is visible once, disappears after a decision, appears in History,
and opens the correct existing review surface.

**Suggested commit:** `feat(verification): add unified review queue`

## Phase 8: Full Verification and Release Preparation

Requirement: 16 and all properties P21-P31.

- [ ] 8.1 Run all new property tests with at least 100 examples.
- [ ] 8.2 Run the complete existing backend suite and address regressions without weakening tests.
- [ ] 8.3 Run frontend unit tests, typecheck, and production build.
- [ ] 8.4 Manually load every legacy path and deep link directly.
- [ ] 8.5 Test mobile navigation, context tabs, pagination, microphone denial/no-device, retries,
      binary unavailable, and retention settings.
- [ ] 8.6 Verify that all configured test providers are mock and that no paid/network call occurs.
- [ ] 8.7 Review migrations against PostgreSQL/Neon and rehearse upgrade/rollback on a copy.
- [ ] 8.8 Update README, architecture notes, and deployment environment examples.

**Gate:** the requirements trace cleanly to passing evidence and the release can be deployed without
manual database repair.

**Suggested commit:** `test(workspace): verify capture consolidation requirements`
