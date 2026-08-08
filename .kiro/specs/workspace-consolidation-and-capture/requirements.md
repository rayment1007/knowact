# Requirements Document

## Introduction

This feature set makes KnowAct easier to navigate and lets users capture information that is
currently unsupported, without weakening the platform's core discipline: every AI-produced
artifact is persisted as `SUGGESTED` and only a human confirmation turns it into fact that can
ground a later answer.

Seven bodies of work are in scope:

1. **Navigation consolidation.** The sidebar is reduced from eleven flat entries to six top-level
   entries (Dashboard, Sources, Knowledge, Actions, Copilot, Settings), each grouping the pages
   that already exist. Every existing route and every Copilot citation `deep_link` continues to
   resolve.
2. **Image capture with OCR.** A user uploads a photo or scan; text is extracted by an
   OCR provider and recorded as an unconfirmed extraction.
3. **Audio capture with transcription.** A user uploads an audio file; speech is transcribed by an
   ASR provider and recorded as an unconfirmed extraction.
4. **Microphone capture.** A user records speech directly in the Source Inbox; the recording is
   transcribed and recorded as an unconfirmed extraction.
5. **Verification queue.** A single place to see what has not yet been verified across
   classifications, knowledge, actions, decisions, email drafts, and extracted text, plus a view of
   what has already been verified.
6. **Pagination.** Every list endpoint returns a bounded page instead of every org-scoped row, and
   every list screen offers next/previous navigation.
7. **Upload validation (defect fix).** Today `POST /api/documents` accepts any MIME type and any
   size. `parse_document` falls through to `_parse_text`, whose `latin-1` attempt effectively never
   raises, so binary image bytes decode into mojibake, get chunked, embedded, and become
   retrievable "knowledge". This silently poisons the retrieval index. Uploads must be validated
   against an explicit allow-list with a size bound, and an unsupported or undecodable payload must
   be rejected or marked `FAILED` rather than producing chunks.

The extraction work deliberately reuses the existing `SUGGESTED` → `CONFIRMED` lifecycle,
`organization_id` scoping through `scope_select`, cross-tenant `404` semantics, `AuditLog`
completeness, and the mock-provider-by-default testing rule. It introduces no new trust model.

## Glossary

- **OCR (Optical Character Recognition)**: Machine extraction of text from a raster image.
- **OCR_Provider**: The abstraction that turns image bytes into `Extracted_Text` plus an optional
  provider confidence value. Has a deterministic mock implementation and a real implementation
  selected by configuration, mirroring `AIProvider` / `EmbeddingProvider`.
- **ASR (Automatic Speech Recognition) / Speech-to-Text**: Machine transcription of spoken audio
  into text.
- **ASR_Provider**: The abstraction that turns audio bytes into `Extracted_Text` plus an optional
  provider confidence value, with the same mock/real pairing as `OCR_Provider`.
- **Media_Asset**: A `DocumentAsset` whose `mime_type` belongs to the image or audio families. It
  carries the same lifecycle statuses (`UPLOADED`, `PARSING`, `CHUNKING`, `EMBEDDING`, `INDEXED`,
  `FAILED`) as a text document.
- **Original_Binary**: The uploaded bytes held by the `StorageBackend` under `storage_root`. On the
  Render deployment the filesystem is ephemeral, so an `Original_Binary` may disappear while its
  derived rows in Postgres survive.
- **Extracted_Text**: The plain-text output of an `OCR_Provider` or `ASR_Provider` run.
- **Extracted_Text_Suggestion**: The persisted, reviewable record of one `Extracted_Text` run. It
  is AI-inferred, not ground truth, and therefore carries `status` in `SuggestionStatus`
  (`SUGGESTED` / `CONFIRMED` / `REJECTED`), the extraction confidence when the provider supplies
  one, and a reference to its originating `Media_Asset`.
- **Suggested_Artifact**: A record produced wholly or partly by an AI capability whose `status` is
  `SUGGESTED`. It is a proposal only: it is not fact, it is not Copilot grounding evidence, and it
  is not retrievable knowledge.
- **Confirmed_Artifact**: A record whose `status` is `CONFIRMED` because an identified human
  explicitly confirmed it. Only a Confirmed_Artifact may be treated as fact, be embedded for
  retrieval, and be cited as Copilot grounding evidence.
- **Capture_Session**: One browser microphone recording, from permission grant through stop, that
  produces a single audio payload.
- **Upload_Allow_List**: The explicit, closed set of `(mime_type, extension)` pairs the system
  accepts for upload, together with a per-family byte-size ceiling.
- **Mojibake**: Text produced by decoding bytes with the wrong character encoding; it is
  syntactically valid text but semantically meaningless.
- **Nav_Group**: One of the six top-level sidebar entries, each expanding to one or more existing
  pages.
- **Deep_Link**: A relative application URL emitted by the Copilot as a citation target, for
  example `/knowledge/{id}`, `/actions/{id}`, `/decisions/{id}`, `/emails/{id}`, `/calendar/{id}`,
  or `/knowledge?business_entity_id={id}`.
- **Page**: One bounded, ordered slice of a list result, described by `limit`, `offset`, and the
  list's deterministic sort order.
- **Page_Size**: The number of rows requested for one `Page`, supplied as `limit`.
- **Offset**: The zero-based count of rows skipped before the first row of a `Page`.
- **Page_Envelope**: The response shape for a paginated list: `items`, `limit`, `offset`,
  `total_count`, and `has_more`.
- **Page_Cursor**: An opaque position token encoding the sort key and row identifier of the last row
  of a delivered Page, used to request the rows that follow that position in the list's total
  ordering. Pagination in this feature is Offset-based; the Page_Cursor is defined here only as the
  named alternative considered and deferred (see the Decisions appendix).
- **Media_Retention_Policy**: The per-user, per-organization setting governing whether an
  Original_Binary is retained after extraction succeeds or discarded, presented on the Privacy &
  Data page beside the existing `RawEmailRetentionPolicy`.
- **Verification_Queue**: The org-scoped, paginated view of every artifact whose `status` is
  `SUGGESTED`, paired with a companion view of artifacts whose `status` is `CONFIRMED` or
  `REJECTED`.
- **Verifiable_Artifact**: Any row participating in the `SUGGESTED` → `CONFIRMED` lifecycle:
  `ClassificationResult`, `KnowledgeItem`, `ActionItem`, `DecisionRecord`, `EmailDraft`, and
  `Extracted_Text_Suggestion`.
- **System**: The KnowAct backend application as a whole, including its configuration, dependency
  wiring, and HTTP layer.
- **Document_Service**: The backend service owning the Media_Asset and document lifecycle
  (`backend/app/modules/cwi/services/document_service.py`), extended by this feature to own
  extraction and Extracted_Text_Suggestion state transitions.
- **Upload_Validator**: The backend component, invoked before any row is written, that decides
  whether a submitted payload is acceptable by type and by byte length.
- **Verification_Queue_Service**: The backend service that aggregates Verifiable_Artifacts across
  artifact types into the Verification_Queue under organization scoping.
- **Privacy_Service**: The backend service behind the Privacy & Data page, extended by this feature
  to read and write the Media_Retention_Policy.
- **App_Shell**: The frontend layout component (`frontend/src/layouts/AppShell.tsx`) that renders
  the sidebar from its navigation definition.
- **Media_Capture_UI**: The Source Inbox and Documents frontend components that upload files, run a
  Capture_Session, and display Media_Asset state.
- **Frontend_Router**: The client-side route table (`frontend/src/App.tsx`) that maps a browser path
  to a page component.
- **Workspace_UI**: The frontend single-page application as a whole, including every list screen and
  its pagination controls.
- **List_API**: Any backend GET route that returns a collection of org-scoped rows.
- **Automated_Test_Suite**: The backend `pytest` suite plus the frontend typecheck and unit tests.

## Requirements

### Requirement 1: Consolidated Six-Entry Navigation

**User Story:** As a workspace user, I want a sidebar with six grouped entries instead of eleven
flat ones, so that I can find a page by purpose rather than by scanning a long list.

#### Acceptance Criteria

1. THE App_Shell SHALL render exactly six top-level Nav_Group entries, named Dashboard, Sources,
   Knowledge, Actions, Copilot, and Settings.
2. THE App_Shell SHALL place the Dashboard page under the Dashboard Nav_Group.
3. THE App_Shell SHALL place the Source Inbox, Gmail Sync, and Documents pages under the Sources
   Nav_Group.
4. THE App_Shell SHALL place the Knowledge Hub and Decision Memory pages under the Knowledge
   Nav_Group.
5. THE App_Shell SHALL place the Action Center page under the Actions Nav_Group.
6. THE App_Shell SHALL place the Copilot and Gmail AI Drafts pages under the Copilot Nav_Group.
7. THE App_Shell SHALL place the Integrations and Privacy & Data pages under the Settings
   Nav_Group.
8. THE App_Shell SHALL render every one of the eleven existing pages under exactly one Nav_Group.
9. WHEN the active route belongs to a Nav_Group, THE App_Shell SHALL mark that Nav_Group as active
   and reveal the pages it contains.
10. WHEN a user activates a Nav_Group that contains more than one page, THE App_Shell SHALL
    navigate to the first page listed for that Nav_Group.
11. WHILE the viewport width is below 768 pixels, THE App_Shell SHALL keep all six Nav_Group
    entries reachable.
12. THE App_Shell SHALL expose the Verification_Queue as a page under the Knowledge Nav_Group.

### Requirement 2: Route and Deep-Link Compatibility

**User Story:** As a Copilot user, I want every citation link and bookmarked URL to keep working
after the navigation change, so that grounded evidence stays reachable.

#### Acceptance Criteria

1. THE Frontend_Router SHALL resolve each of the paths `/`, `/source-inbox`, `/knowledge`,
   `/actions`, `/decisions`, `/integrations`, `/gmail`, `/documents`, `/copilot`, `/email-drafts`,
   and `/privacy` to the same page each resolved to before this feature.
2. WHERE a page is relocated to a new path, THE Frontend_Router SHALL resolve the previous path to
   the new path by redirect.
3. THE Frontend_Router SHALL resolve a Deep_Link of the form `/knowledge/{id}` to the Knowledge Hub
   page with the identified `KnowledgeItem` selected.
4. THE Frontend_Router SHALL resolve a Deep_Link of the form `/actions/{id}` to the Action Center
   page with the identified `ActionItem` selected.
5. THE Frontend_Router SHALL resolve a Deep_Link of the form `/decisions/{id}` to the Decision
   Memory page with the identified `DecisionRecord` selected.
6. THE Frontend_Router SHALL resolve a Deep_Link of the form `/emails/{id}` to a page that displays
   the identified email record.
7. THE Frontend_Router SHALL resolve a Deep_Link of the form `/calendar/{id}` to a page that
   displays the identified calendar event link.
8. THE Frontend_Router SHALL resolve a Deep_Link of the form `/knowledge?business_entity_id={id}` to
   the Knowledge Hub page filtered to the identified business entity.
9. IF a Deep_Link identifier does not resolve to a row readable by the caller's organization, THEN
   THE Frontend_Router SHALL display a not-found message on the target page and keep the
   surrounding navigation usable.
10. IF a requested path matches no route, THEN THE Frontend_Router SHALL redirect the browser to
    `/`.

### Requirement 3: Upload Type and Size Validation

**User Story:** As a knowledge owner, I want unsupported or oversized uploads rejected up front, so
that undecodable bytes never become searchable mojibake in the retrieval index.

#### Acceptance Criteria

1. THE Upload_Validator SHALL define an Upload_Allow_List containing, at minimum, `application/pdf`,
   `text/plain`, `text/markdown`, `text/csv`,
   `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `message/rfc822`,
   `image/png`, `image/jpeg`, `image/webp`, `image/tiff`, `audio/wav`, `audio/mpeg`, `audio/mp4`,
   `audio/webm`, and `audio/ogg`.
2. WHEN a file is submitted to `POST /api/documents`, THE Upload_Validator SHALL determine the
   effective type from the declared `content_type`, the filename extension, and the payload's
   leading bytes.
3. IF the effective type of a submitted file is absent from the Upload_Allow_List, THEN THE
   Upload_Validator SHALL reject the request with HTTP `415` and a message naming the supported
   types.
4. IF the declared `content_type` and the payload's leading bytes indicate different families among
   text, document, image, and audio, THEN THE Upload_Validator SHALL reject the request with HTTP
   `415`.
5. THE Upload_Validator SHALL reject with HTTP `413` any submitted file whose byte length exceeds
   the ceiling configured for its family.
6. THE Upload_Validator SHALL apply a default ceiling of 20 MB for text and document families,
   10 MB for the image family, and 25 MB for the audio family.
7. THE Upload_Validator SHALL reject with HTTP `400` any submitted file whose byte length is zero.
8. WHEN the Upload_Validator rejects a request, THE Document_Service SHALL leave no `DocumentAsset`
   row, no `DocumentChunk` row, and no stored Original_Binary behind.
9. THE Document_Service SHALL dispatch parsing by the validated effective type rather than by
   substring inspection of the declared `content_type`.
10. IF a payload of a text or document type cannot be decoded or parsed into text without character
    replacement, THEN THE Document_Service SHALL set the asset's `processing_status` to `FAILED`
    and persist zero `DocumentChunk` rows for that asset.
11. THE Document_Service SHALL persist zero `DocumentChunk` rows for any asset whose
    `processing_status` is `FAILED`.
12. WHEN an asset is marked `FAILED`, THE Document_Service SHALL retain a human-readable failure
    reason on the asset so a user can decide whether to retry.

### Requirement 4: Image Upload with OCR Text Extraction

**User Story:** As a user holding a printed page or a whiteboard photo, I want to upload the image
and have its text extracted, so that the information becomes searchable without retyping.

#### Acceptance Criteria

1. WHEN a user uploads a file whose validated effective type is in the image family, THE
   Document_Service SHALL persist a Media_Asset with `processing_status` set to `UPLOADED`.
2. WHEN a Media_Asset of the image family is processed, THE Document_Service SHALL invoke the
   OCR_Provider exactly once for that asset.
3. WHEN the OCR_Provider returns Extracted_Text containing at least one non-whitespace character,
   THE Document_Service SHALL persist exactly one Extracted_Text_Suggestion for that Media_Asset
   with `status` set to `SUGGESTED`.
4. WHERE the OCR_Provider supplies a confidence value, THE Document_Service SHALL persist that
   value on the Extracted_Text_Suggestion in the closed interval 0.0 to 1.0.
5. WHERE the OCR_Provider supplies no confidence value, THE Document_Service SHALL persist the
   Extracted_Text_Suggestion with an absent confidence value.
6. THE Document_Service SHALL persist on every Extracted_Text_Suggestion the identifier of the
   Media_Asset it was derived from and the extraction method, which is `OCR` for images.
7. IF the OCR_Provider returns Extracted_Text with no non-whitespace character, THEN THE
   Document_Service SHALL set the Media_Asset `processing_status` to `FAILED`, record the reason
   "no text detected", and persist zero `DocumentChunk` rows.
8. IF the OCR_Provider raises an error or exceeds its configured timeout, THEN THE Document_Service
   SHALL set the Media_Asset `processing_status` to `FAILED` and leave the asset retriable.
9. WHEN a Media_Asset of the image family is processed a second time and it already holds an
   Extracted_Text_Suggestion, THE Document_Service SHALL return the existing state and SHALL invoke
   the OCR_Provider zero additional times.
10. THE Document_Service SHALL apply the caller-supplied `sensitivity` value to a Media_Asset of the
    image family using the same rules it applies to a text document.
11. WHEN an image upload is recorded, THE Workspace_UI SHALL display the resulting Media_Asset on
    the Documents page together with its `processing_status`.

### Requirement 5: Audio Upload with Speech-to-Text Transcription

**User Story:** As a user with a recorded call or voice memo, I want to upload the audio and have it
transcribed, so that what was said becomes searchable text.

#### Acceptance Criteria

1. WHEN a user uploads a file whose validated effective type is in the audio family, THE
   Document_Service SHALL persist a Media_Asset with `processing_status` set to `UPLOADED`.
2. WHEN a Media_Asset of the audio family is processed, THE Document_Service SHALL invoke the
   ASR_Provider exactly once for that asset.
3. WHEN the ASR_Provider returns Extracted_Text containing at least one non-whitespace character,
   THE Document_Service SHALL persist exactly one Extracted_Text_Suggestion for that Media_Asset
   with `status` set to `SUGGESTED`.
4. THE Document_Service SHALL persist the extraction method `ASR` on every Extracted_Text_Suggestion
   derived from an audio Media_Asset.
5. WHERE the ASR_Provider supplies a confidence value, THE Document_Service SHALL persist that value
   on the Extracted_Text_Suggestion in the closed interval 0.0 to 1.0.
6. WHERE the ASR_Provider supplies a detected language code, THE Document_Service SHALL persist that
   code on the Extracted_Text_Suggestion.
7. IF the ASR_Provider returns Extracted_Text with no non-whitespace character, THEN THE
   Document_Service SHALL set the Media_Asset `processing_status` to `FAILED`, record the reason
   "no speech detected", and persist zero `DocumentChunk` rows.
8. IF the ASR_Provider raises an error or exceeds its configured timeout, THEN THE Document_Service
   SHALL set the Media_Asset `processing_status` to `FAILED` and leave the asset retriable.
9. WHEN a Media_Asset of the audio family is processed a second time and it already holds an
   Extracted_Text_Suggestion, THE Document_Service SHALL return the existing state and SHALL invoke
   the ASR_Provider zero additional times.
10. THE Document_Service SHALL reject with HTTP `413` an audio payload whose byte length exceeds the
    audio family ceiling defined in Requirement 3.

### Requirement 6: Microphone Capture in the Source Inbox

**User Story:** As a user who would rather speak than type, I want a microphone control in the
Source Inbox that turns my speech into a recorded source item, so that I can capture a thought in
one step.

#### Acceptance Criteria

1. THE Media_Capture_UI SHALL render a microphone control on the Source Inbox page.
2. WHEN a user activates the microphone control, THE Media_Capture_UI SHALL request browser
   microphone permission before starting a Capture_Session.
3. IF the browser denies microphone permission, THEN THE Media_Capture_UI SHALL display a message
   stating that permission was denied and SHALL offer the existing manual text entry path.
4. IF the browser exposes no microphone recording capability, THEN THE Media_Capture_UI SHALL hide
   the microphone control and SHALL keep the manual text entry path available.
5. IF no microphone input device is available, THEN THE Media_Capture_UI SHALL display a message
   stating that no microphone was found.
6. WHILE a Capture_Session is recording, THE Media_Capture_UI SHALL display a recording indicator
   and the elapsed recording duration.
7. WHILE a Capture_Session is recording, THE Media_Capture_UI SHALL offer a stop control and a
   discard control.
8. WHEN a user activates the discard control, THE Media_Capture_UI SHALL end the Capture_Session
   and SHALL send no audio to the backend.
9. WHEN the elapsed duration of a Capture_Session reaches 300 seconds, THE Media_Capture_UI SHALL
   stop the recording automatically and retain the audio captured so far.
10. WHEN a user stops a Capture_Session whose elapsed duration is at least 1 second, THE
    Media_Capture_UI SHALL upload the captured audio to the audio upload endpoint as a Media_Asset
    of the audio family.
11. IF a user stops a Capture_Session whose elapsed duration is below 1 second, THEN THE
    Media_Capture_UI SHALL discard the audio and SHALL display a message stating the recording was
    too short.
12. WHEN a Capture_Session upload succeeds, THE Document_Service SHALL transcribe it under the rules
    of Requirement 5 and SHALL persist the resulting Extracted_Text_Suggestion with `status` set to
    `SUGGESTED`.
13. THE Document_Service SHALL retain the Capture_Session Original_Binary in the `StorageBackend`
    until the user or a retention policy deletes it.
14. WHILE a Capture_Session upload or transcription is in progress, THE Media_Capture_UI SHALL
    display a pending state for that capture.
15. IF a Capture_Session upload fails, THEN THE Media_Capture_UI SHALL display a retry control and
    SHALL retain the captured audio in the browser until the user retries or discards it.

### Requirement 7: Review and Confirmation of Extracted Text

**User Story:** As a reviewer, I want to read, edit, confirm, or reject text that OCR or
transcription produced, so that machine-read text never becomes fact without a human decision.

#### Acceptance Criteria

1. THE Document_Service SHALL create every Extracted_Text_Suggestion with `status` set to
   `SUGGESTED`.
2. THE Document_Service SHALL treat an Extracted_Text_Suggestion whose `status` is `SUGGESTED` as
   ineligible for Copilot grounding evidence.
3. WHEN a user confirms an Extracted_Text_Suggestion, THE Document_Service SHALL set its `status` to
   `CONFIRMED` and SHALL record the confirming user and the confirmation timestamp.
4. WHEN a user confirms an Extracted_Text_Suggestion, THE Document_Service SHALL create the
   `SourceItem` for that Media_Asset with the confirmed text as its content.
5. WHEN a user edits the text of an Extracted_Text_Suggestion before confirming, THE
   Document_Service SHALL persist the edited text, retain the original machine-produced text, and
   record that the text was edited by a human.
6. WHEN a user rejects an Extracted_Text_Suggestion, THE Document_Service SHALL set its `status` to
   `REJECTED` and SHALL persist zero `DocumentChunk` rows derived from that suggestion.
7. WHEN an Extracted_Text_Suggestion reaches `status` `CONFIRMED`, THE Document_Service SHALL chunk
   and embed the confirmed text and SHALL advance the Media_Asset `processing_status` to `INDEXED`.
8. WHILE an Extracted_Text_Suggestion `status` is `SUGGESTED` or `REJECTED`, THE Document_Service
   SHALL keep the derived text out of retrieval results.
9. WHEN an Extracted_Text_Suggestion is confirmed or rejected, THE Document_Service SHALL write
   exactly one `AuditLog` row in the same database transaction as the status change.
10. WHEN an Extracted_Text_Suggestion is confirmed a second time, THE Document_Service SHALL return
    the existing confirmed state, SHALL create no duplicate `SourceItem`, SHALL create no duplicate
    `DocumentChunk` rows, and SHALL write no additional `AuditLog` row.
11. THE Document_Service SHALL expose on every Extracted_Text_Suggestion its extraction method, its
    confidence value when present, and the identifier of its originating Media_Asset.
12. WHILE an Extracted_Text_Suggestion `status` is `SUGGESTED`, THE Workspace_UI SHALL label its
    Extracted_Text as machine-extracted and unverified.

### Requirement 8: OCR and ASR Provider Abstraction

**User Story:** As a maintainer, I want OCR and speech-to-text behind the same provider abstraction
as the existing AI and embedding capabilities, so that the whole test suite runs deterministically
with no paid or networked calls.

#### Acceptance Criteria

1. THE OCR_Provider SHALL be defined as a protocol with a mock implementation and a real
   implementation.
2. THE ASR_Provider SHALL be defined as a protocol with a mock implementation and a real
   implementation.
3. THE System SHALL select the OCR_Provider implementation from a configuration setting whose
   default value is `mock`.
4. THE System SHALL select the ASR_Provider implementation from a configuration setting whose
   default value is `mock`.
5. WHEN the mock OCR_Provider receives identical image bytes twice, THE mock OCR_Provider SHALL
   return identical Extracted_Text both times.
6. WHEN the mock ASR_Provider receives identical audio bytes twice, THE mock ASR_Provider SHALL
   return identical Extracted_Text both times.
7. THE mock OCR_Provider and THE mock ASR_Provider SHALL each complete without opening a network
   connection.
8. THE mock and real implementations of the OCR_Provider SHALL accept the same inputs and return the
   same result shape.
9. THE mock and real implementations of the ASR_Provider SHALL accept the same inputs and return the
   same result shape.
10. WHERE a real OCR_Provider or ASR_Provider is configured without its credential, THE System SHALL
    raise a configuration error naming the missing setting at provider construction.
11. WHILE `mode` is `PRODUCTION`, IF a real OCR_Provider or ASR_Provider call fails, THEN THE System
    SHALL surface a retriable HTTP `503` and SHALL leave the Media_Asset retriable.
12. WHILE `mode` is `DEVELOPMENT`, IF a real OCR_Provider or ASR_Provider call fails, THEN THE
    System SHALL fall back to the corresponding mock implementation and SHALL record that the
    fallback occurred.
13. THE OCR_Provider and THE ASR_Provider SHALL apply the shared operational-support system framing
    used by the existing `AIProvider` implementations.
14. THE System SHALL enforce a configured per-call timeout on every real OCR_Provider and
    ASR_Provider invocation.

### Requirement 9: Behaviour When an Original Binary Is Unavailable

**User Story:** As a user on the hosted deployment, I want extracted text and provenance to stay
usable after the server's ephemeral filesystem loses my original image or audio, so that a restart
does not erase my captured knowledge.

#### Acceptance Criteria

1. THE Document_Service SHALL retain in Postgres, independent of the Original_Binary, each
   Media_Asset's metadata, its Extracted_Text_Suggestion, its confirmed text, its
   `DocumentChunk` rows, its embeddings, and its provenance references.
2. WHILE an Original_Binary is absent from the `StorageBackend`, THE Document_Service SHALL continue
   to serve the Media_Asset's metadata and Extracted_Text_Suggestion.
3. WHILE an Original_Binary is absent from the `StorageBackend`, THE Document_Service SHALL continue
   to return the Media_Asset's `DocumentChunk` rows as retrieval evidence when the confirmation and
   sensitivity rules permit.
4. WHEN a client requests a Media_Asset whose Original_Binary is absent, THE Document_Service SHALL
   report the Original_Binary availability as unavailable in the response.
5. WHILE a Media_Asset reports its Original_Binary as unavailable, THE Workspace_UI SHALL
   display a placeholder stating the original file is no longer stored and SHALL keep the extracted
   text visible.
6. WHILE a Media_Asset reports its Original_Binary as unavailable, THE Workspace_UI SHALL
   disable any control that would download or re-process the original.
7. IF processing is requested for a Media_Asset whose Original_Binary is absent and whose
   Extracted_Text_Suggestion does not exist, THEN THE Document_Service SHALL set
   `processing_status` to `FAILED` with the reason "original file unavailable".
8. WHEN a user deletes a Media_Asset, THE Document_Service SHALL delete its Original_Binary, its
   `DocumentChunk` rows, its embeddings, and its Extracted_Text_Suggestion, and SHALL write exactly
   one `AuditLog` row in the same transaction.

### Requirement 10: Verification Queue

**User Story:** As a reviewer, I want one place that shows everything still awaiting verification
and everything already decided, so that nothing sits unconfirmed without me noticing.

#### Acceptance Criteria

1. THE Verification_Queue_Service SHALL expose a paginated endpoint returning Verifiable_Artifacts
   whose `status` is `SUGGESTED` for the caller's organization.
2. THE Verification_Queue_Service SHALL expose a paginated endpoint returning Verifiable_Artifacts
   whose `status` is `CONFIRMED` or `REJECTED` for the caller's organization.
3. THE Verification_Queue_Service SHALL include `ClassificationResult`, `KnowledgeItem`,
   `ActionItem`, `DecisionRecord`, `EmailDraft`, and Extracted_Text_Suggestion rows among
   Verifiable_Artifacts.
4. THE Verification_Queue_Service SHALL return for each queue entry its artifact type, its
   identifier, its `status`, its creation timestamp, a human-readable summary, and the application
   path that opens the artifact for review.
5. THE Verification_Queue_Service SHALL order unverified entries by creation timestamp ascending
   with the artifact identifier as a deterministic tie-break.
6. WHERE the caller supplies an artifact-type filter, THE Verification_Queue_Service SHALL return
   only entries of the requested types.
7. THE Verification_Queue_Service SHALL return the count of unverified entries per artifact type.
8. WHEN a Verifiable_Artifact `status` changes from `SUGGESTED` to `CONFIRMED` or `REJECTED`, THE
   Verification_Queue_Service SHALL exclude that artifact from the unverified result on the next
   request and SHALL include it in the verified result.
9. THE App_Shell SHALL display the count of unverified entries on the Verification_Queue navigation
   entry.
10. WHEN a user activates a queue entry, THE Frontend_Router SHALL navigate to the existing review
    page for that artifact type with the artifact selected.
11. THE Verification_Queue_Service SHALL return only rows whose `organization_id` equals the
    caller's `organization_id`.
12. WHEN a request to a Verification_Queue endpoint carries no valid session, THE
    Verification_Queue_Service SHALL respond with HTTP `401`.

### Requirement 11: Paginated List Endpoints

**User Story:** As a user of a workspace that keeps growing, I want list endpoints to return one
bounded page at a time, so that screens stay responsive and predictable.

#### Acceptance Criteria

1. THE List_API SHALL accept an optional `limit` query parameter and an optional `offset` query
   parameter on every route that returns a collection of org-scoped rows.
2. WHERE `limit` is absent, THE List_API SHALL apply a Page_Size of 25.
3. WHERE `offset` is absent, THE List_API SHALL apply an Offset of 0.
4. THE List_API SHALL accept `limit` values from 1 through 100 inclusive.
5. IF `limit` is outside 1 through 100 inclusive, THEN THE List_API SHALL respond with HTTP `422`.
6. IF `offset` is negative, THEN THE List_API SHALL respond with HTTP `422`.
7. THE List_API SHALL return a Page_Envelope containing `items`, `limit`, `offset`, `total_count`,
   and `has_more`.
8. THE List_API SHALL set `total_count` to the number of rows matching the request's filters and
   organization scope at the time the query runs.
9. THE List_API SHALL set `has_more` to true when `offset` plus the length of `items` is less than
   `total_count`, and to false otherwise.
10. THE List_API SHALL return at most `limit` rows in `items`.
11. THE List_API SHALL apply a deterministic total ordering to every paginated query, using a
    declared sort column and the row identifier as the final tie-break.
12. WHEN a client requests consecutive Pages of the same unchanged collection, THE List_API SHALL
    return disjoint `items` sets whose union equals the full matching collection.
13. WHEN rows are inserted between two Page requests, THE List_API SHALL still return only rows
    matching the request's filters and organization scope.
14. THE List_API SHALL return only rows whose `organization_id` equals the caller's
    `organization_id` for every `limit` and `offset` combination.
15. IF `offset` is greater than or equal to `total_count`, THEN THE List_API SHALL return an empty
    `items` list with `has_more` set to false.
16. THE List_API SHALL apply every existing filter, permission, and sensitivity predicate before
    applying `limit` and `offset`.

### Requirement 12: Paginated List Presentation

**User Story:** As a user browsing a long list, I want next and previous controls with a clear
position indicator, so that I can move through results without loading everything.

#### Acceptance Criteria

1. THE Workspace_UI SHALL render pagination controls on every screen backed by a paginated
   List_API route.
2. THE Workspace_UI SHALL display the current Page position and the `total_count` returned by
   the List_API.
3. WHEN a user activates the next control, THE Workspace_UI SHALL request the following Page
   using the same `limit` and an `offset` increased by `limit`.
4. WHEN a user activates the previous control, THE Workspace_UI SHALL request the preceding Page
   using the same `limit` and an `offset` decreased by `limit`.
5. WHILE `has_more` is false, THE Workspace_UI SHALL disable the next control.
6. WHILE `offset` equals 0, THE Workspace_UI SHALL disable the previous control.
7. WHEN a user changes a filter, THE Workspace_UI SHALL reset `offset` to 0 before requesting
   the list.
8. WHEN a user confirms, rejects, or deletes a row on a paginated screen, THE Workspace_UI SHALL
   re-request the current Page.
9. IF a re-requested Page returns an empty `items` list and `offset` is greater than 0, THEN THE
   Workspace_UI SHALL request the preceding Page.
10. WHILE a Page request is in flight, THE Workspace_UI SHALL display a loading state for the
    list region.

### Requirement 13: Tenant Isolation and Authorization for New Endpoints

**User Story:** As a security owner, I want every new endpoint to obey the existing tenant rules, so
that capture and verification introduce no cross-tenant path.

#### Acceptance Criteria

1. THE System SHALL require a valid session on every endpoint introduced by this feature and SHALL
   respond with HTTP `401` when the session is missing or invalid.
2. THE System SHALL resolve every new query through the existing organization-scoped query helper.
3. WHEN a caller requests a Media_Asset, an Extracted_Text_Suggestion, or a Verification_Queue entry
   whose `organization_id` differs from the caller's `organization_id`, THE System SHALL respond
   with HTTP `404`.
4. THE System SHALL persist the caller's `organization_id` on every Media_Asset and
   Extracted_Text_Suggestion row it creates.
5. THE System SHALL exclude the Original_Binary bytes from every API response body.
6. THE System SHALL exclude provider credentials and provider keys from every API response body.
7. WHEN a mutation introduced by this feature succeeds, THE System SHALL write exactly one
   `AuditLog` row in the same database transaction as the mutation.

### Requirement 14: Extraction Execution Model and Visible Progress

**User Story:** As a user who just uploaded an image or a recording, I want to know whether
extraction has run, is running, or failed, so that I am never left guessing whether my capture was
processed.

#### Acceptance Criteria

1. THE Document_Service SHALL run extraction in a request-scoped processing step invoked by
   `POST /api/documents/{id}/process`, using the same explicit two-step upload-then-process shape as
   the existing text document path.
2. WHEN an upload succeeds, THE Document_Service SHALL return the Media_Asset with
   `processing_status` set to `UPLOADED` without invoking the OCR_Provider or the ASR_Provider.
3. WHILE an extraction request is executing, THE Document_Service SHALL record the Media_Asset
   `processing_status` as `PARSING`.
4. WHEN extraction succeeds, THE Document_Service SHALL set the Media_Asset `processing_status` to a
   value distinct from `INDEXED` that denotes text extracted and awaiting human verification.
5. THE Document_Service SHALL set a Media_Asset `processing_status` to `INDEXED` only after its
   Extracted_Text_Suggestion reaches `status` `CONFIRMED` and its chunks are embedded.
6. WHILE a Media_Asset `processing_status` is `UPLOADED` or `PARSING`, THE Workspace_UI SHALL
   display that status for the asset and SHALL offer no confirm control for it.
7. WHEN a Media_Asset `processing_status` is `FAILED`, THE Workspace_UI SHALL display the recorded
   failure reason and SHALL offer a control that re-invokes the processing step.
8. THE Document_Service SHALL enforce a configured extraction timeout and SHALL set the Media_Asset
   `processing_status` to `FAILED` with a timeout reason when that timeout elapses.
9. WHEN a client uploads a Media_Asset and the Media_Capture_UI submits it, THE Media_Capture_UI
   SHALL invoke the processing step for that asset without further user action.
10. WHEN a processing request for a Media_Asset whose `processing_status` is `PARSING` is received,
    THE Document_Service SHALL leave the existing extraction attempt as the only one in flight and
    SHALL invoke the OCR_Provider or ASR_Provider zero additional times.
11. THE Document_Service SHALL commit the Media_Asset status transition and the
    Extracted_Text_Suggestion insert in the same database transaction.

### Requirement 15: Original Media Retention

**User Story:** As a privacy-conscious user, I want to choose whether my original photo or recording
is kept after its text has been extracted, so that I control how long raw media lives on the server.

#### Acceptance Criteria

1. THE Privacy_Service SHALL persist a Media_Retention_Policy per user and organization whose value
   is either `RETAIN_ORIGINAL` or `DISCARD_AFTER_EXTRACTION`.
2. THE Privacy_Service SHALL apply `RETAIN_ORIGINAL` as the Media_Retention_Policy default.
3. THE Privacy_Service SHALL present the Media_Retention_Policy on the Privacy & Data page beside
   the existing raw-email retention policy and SHALL allow a user to change it.
4. WHERE the Media_Retention_Policy is `DISCARD_AFTER_EXTRACTION`, WHEN extraction succeeds and its
   Extracted_Text_Suggestion is persisted, THE Document_Service SHALL delete the Original_Binary
   from the `StorageBackend` and SHALL mark the Media_Asset's Original_Binary as unavailable.
5. WHERE the Media_Retention_Policy is `DISCARD_AFTER_EXTRACTION` and extraction did not succeed,
   THE Document_Service SHALL retain the Original_Binary so the failed extraction stays retriable.
6. WHERE the Media_Retention_Policy is `RETAIN_ORIGINAL`, THE Document_Service SHALL retain the
   Original_Binary until a user deletes the Media_Asset.
7. WHEN the Document_Service deletes an Original_Binary under the Media_Retention_Policy, THE
   Document_Service SHALL write exactly one `AuditLog` row in the same transaction.
8. THE Document_Service SHALL complete confirmation, chunking, embedding, retrieval, and
   Verification_Queue listing for a Media_Asset without reading its Original_Binary.
9. WHEN a user changes the Media_Retention_Policy to `DISCARD_AFTER_EXTRACTION`, THE Privacy_Service
   SHALL apply the new value to subsequent extractions and SHALL leave already-stored
   Original_Binary objects for the existing document-deletion path to remove.
10. THE Privacy_Service SHALL exclude Extracted_Text and Extracted_Text_Suggestion rows from every
    Original_Binary deletion performed under the Media_Retention_Policy.

### Requirement 16: Verification and Test Constraints

**User Story:** As a maintainer, I want this feature covered by the same testing discipline as the
rest of KnowAct, so that its invariants stay enforced as the code changes.

#### Acceptance Criteria

1. THE Automated_Test_Suite SHALL complete every test for this feature without making a paid or real
   external API call.
2. THE Automated_Test_Suite SHALL use the mock OCR_Provider and the mock ASR_Provider by default.
3. THE Automated_Test_Suite SHALL configure every property-based test for this feature to run a
   minimum of 100 examples.
4. THE Automated_Test_Suite SHALL include property-based tests for the new correctness properties
   listed in the appendix of this document.
5. THE Automated_Test_Suite SHALL include a regression test asserting that an image payload
   submitted before this feature's validation existed produces zero `DocumentChunk` rows.
6. THE Automated_Test_Suite SHALL include a test asserting that every one of the paths listed in
   Requirement 2.1 resolves after the navigation change.
7. THE Automated_Test_Suite SHALL keep the existing backend tests passing.
8. THE Automated_Test_Suite SHALL keep the frontend typecheck passing.

## Appendix: Correctness Property Alignment

This appendix records which existing correctness properties the new work must continue to uphold and
which new properties the new behaviour needs. It is informational; the properties themselves belong
to the design document.

### Existing properties this feature must not break

| Property | Statement | Why this feature touches it |
| --- | --- | --- |
| P1 | Confidence values lie in `[0.0, 1.0]` | OCR and ASR confidence values are new confidence carriers (Requirements 4.4, 5.5) |
| P2 | Deterministic mock: identical input yields identical output | The new mock OCR and ASR providers must be deterministic (Requirements 8.5, 8.6) |
| P3 | Privacy gate: private categories never yield a `CONFIRMED` `KnowledgeItem` | Extracted text enters the same pipeline (Requirement 7.4) |
| P4 | Sensitivity gate: `HIGHLY_SENSITIVE` without acknowledgement yields a refusal | Media assets carry `sensitivity` (Requirement 4.10) |
| P5 | Human-in-the-loop: AI output is `SUGGESTED` immediately after generation | Extracted text is AI output (Requirement 7.1) |
| P6 | Audit completeness: a confirmation writes exactly one audit row in the same transaction | New confirm and reject paths (Requirements 7.9, 13.7) |
| P11 / P13 | Org isolation: a row is readable only when `row.organization_id == user.organization_id`; cross-org access returns `404` | New endpoints and paginated queries (Requirements 11.14, 13.3) |
| P12 | Evidence presence: every `CONFIRMED` `KnowledgeItem` has non-empty evidence text | Confirmed extracted text becomes evidence (Requirement 7.7) |
| P18 | Retrieval permission and sensitivity filtering applied as SQL predicates before ranking | Pagination must not widen the predicate set (Requirement 11.16) |
| P19 | Citation grounding: no fabricated citations | Unconfirmed extracted text is not grounding evidence (Requirement 7.2) |
| P20 | Human-in-the-loop and audit completeness across the connected-workspace surface | Extraction and verification mutations join that surface (Requirement 13.7) |

### Proposed new properties

- **P21 — Extracted text starts unverified.** For every generated image or audio payload, the
  Extracted_Text_Suggestion produced by processing has `status == SUGGESTED` immediately after
  processing, and no `SourceItem` or `DocumentChunk` derived from it exists until an explicit
  confirm call. Validates Requirements 7.1, 7.2, 7.4, 7.7.
- **P22 — Unsupported uploads create nothing.** For every generated payload whose effective type is
  outside the Upload_Allow_List, the upload is rejected and the counts of `DocumentAsset`,
  `DocumentChunk`, and stored Original_Binary objects are unchanged. Validates Requirements 3.3,
  3.8.
- **P23 — No mojibake chunks.** For every generated payload that cannot be decoded to text without
  character replacement, the asset ends `FAILED` with zero `DocumentChunk` rows. Validates
  Requirements 3.10, 3.11.
- **P24 — Pages partition the collection.** For every generated collection size and every `limit`
  in 1 through 100, iterating Pages from `offset == 0` while `has_more` is true yields `items` sets
  that are pairwise disjoint and whose union equals the full matching collection exactly once.
  Validates Requirements 11.12, 11.10, 11.15.
- **P25 — Pages never cross tenants.** For every two seeded organizations and every `limit` and
  `offset` combination, no Page returned to a caller of organization A contains a row whose
  `organization_id` is organization B. Validates Requirements 11.14, 13.3.
- **P26 — Extraction is invoked at most once per asset.** For every sequence of `n >= 1` process
  calls on the same Media_Asset, the OCR_Provider or ASR_Provider is invoked at most once and at
  most one Extracted_Text_Suggestion exists for that asset. Validates Requirements 4.9, 5.9.
- **P27 — Confirmation is idempotent.** For every sequence of `n >= 1` confirm calls on the same
  Extracted_Text_Suggestion, exactly one `SourceItem`, one set of `DocumentChunk` rows, and one
  `AuditLog` row result. Validates Requirement 7.10.
- **P28 — Derived rows survive binary loss.** For every processed and confirmed Media_Asset, after
  the Original_Binary is removed from the `StorageBackend`, the asset metadata, its
  Extracted_Text_Suggestion, its `DocumentChunk` rows, and its provenance remain readable, and the
  asset reports its Original_Binary as unavailable. Validates Requirements 9.1, 9.2, 9.4.
- **P29 — Verification queue partitions by status.** For every generated set of Verifiable_Artifacts,
  the unverified queue contains exactly the artifacts whose `status` is `SUGGESTED`, the verified
  queue contains exactly those whose `status` is `CONFIRMED` or `REJECTED`, and the two results are
  disjoint. Validates Requirements 10.1, 10.2, 10.8.
- **P30 — `INDEXED` implies confirmation.** For every generated sequence of upload, process, edit,
  reject, and confirm operations on a Media_Asset, the asset's `processing_status` equals `INDEXED`
  only when its Extracted_Text_Suggestion `status` equals `CONFIRMED`, and no embedded chunk exists
  for an asset whose suggestion is not `CONFIRMED`. Validates Requirements 14.4, 14.5, 7.7.
- **P31 — Discarding the original preserves derived state.** For every processed Media_Asset whose
  owner's Media_Retention_Policy is `DISCARD_AFTER_EXTRACTION`, after successful extraction the
  Original_Binary is absent while the asset metadata and Extracted_Text_Suggestion remain readable,
  and a subsequent confirmation still succeeds. Validates Requirements 15.4, 15.8, 9.2.

### Decisions this document pins down

These were the ambiguous edges; each is now settled by a numbered criterion rather than left to the
design phase.

| Question | Decision | Where | Why |
| --- | --- | --- | --- |
| Are OCR and ASR outputs fact? | No. They are AI-derived and enter as `SUGGESTED`; confirmation is required before they are chunked, embedded, or usable as Copilot grounding. | 7.1, 7.2, 7.7, 7.8 | Preserves the existing human-in-the-loop invariant; machine-read text is exactly the kind of output that needs a human check. |
| What about empty or low-confidence extraction? | An extraction with no non-whitespace character fails explicitly with a stated reason; a supplied confidence is persisted, bounded, and shown beside the unverified label. | 4.7, 5.7, 4.4, 5.5, 7.11, 7.12 | Silent acceptance of an empty extraction is the failure mode that poisons the index. |
| Synchronous or deferred extraction? | An explicit `POST /api/documents/{id}/process` step, mirroring the existing text path, with the retriable `FAILED` state reused. No background worker. | 14.1–14.3, 14.7 | Render's free tier gives no separate worker process, and the two-step shape already exists and is already tested. |
| Is the original image or audio kept? | Retained by default; a per-user Media_Retention_Policy allows discard after successful extraction. Nothing downstream reads the binary. | 15.1, 15.2, 15.4, 15.8 | `STORAGE_BACKEND=local` on an ephemeral filesystem means the binary can vanish regardless, so no operation may depend on it (Requirement 9). |
| Browser-side or backend microphone transcription? | The browser records audio and uploads it to the same audio endpoint; transcription runs server-side through the ASR_Provider. The Web Speech API is not used. | 6.10, 6.12 | One transcription path to test, one provider abstraction, deterministic mock coverage, and no dependence on per-browser speech support. |
| New Verification_Queue page or filtered existing views? | A new page under the Knowledge Nav_Group spanning six artifact types, with entries deep-linking back to each type's existing review page. | 1.12, 10.3, 10.10 | The artifacts live in different modules, so no single existing page can host them; delegating confirmation avoids a second confirmation path. |
| Offset-based or cursor-based pagination? | Offset-based, with a declared sort column plus row identifier as a total-order tie-break. Page_Cursor is defined but deferred. | 11.1–11.3, 11.11, 11.12 | Ordering on a non-unique timestamp alone lets rows shift between pages; the identifier tie-break makes offsets stable enough for these list sizes, and `total_count` is cheap at this scale. |
| Upload size caps? | 20 MB text and document, 10 MB image, 25 MB audio, zero-length rejected. | 3.5–3.7 | Bounded against the 512 MB free-tier instance, since the payload is read into memory before storage. |
| How do new providers stay testable? | OCR_Provider and ASR_Provider are protocols with deterministic mocks selected by default configuration, matching `AIProvider` and `EmbeddingProvider`. | 8.1–8.7, 16.1, 16.2 | No test may make a paid or networked call. |
