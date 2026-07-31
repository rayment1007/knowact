"""Classification service (Requirements 4 and 5).

The :class:`ClassificationService` owns the *classify* step of the pipeline and
the human-in-the-loop confirmation that follows it. It has three public
operations:

* :meth:`classify` — invoke the AI provider to classify a source item and
  persist the result in status ``SUGGESTED`` (Requirements 4.1, 4.2, 5.5). It
  never auto-confirms: a suggestion always waits for an explicit human decision.
* :meth:`confirm` — accept a suggested classification, optionally overriding the
  AI-chosen axes with a human selection, and in **one transaction** set the
  result to ``CONFIRMED``, set the source item to ``CLASSIFIED``, and write
  exactly one ``CONFIRM_CLASSIFICATION`` audit row (Requirements 5.1, 5.2, 5.4).
* :meth:`reject` — decline a suggested classification, setting it to
  ``REJECTED`` while **retaining** the row as a negative signal (Requirement
  5.3).

Two structural invariants run through the service:

* **At most one non-rejected result per item (Requirement 4.5).** The database
  enforces this with a partial unique index over non-``REJECTED`` rows; the
  service upholds it by rejecting any existing active result before persisting a
  fresh suggestion on re-classification. Rejected rows are retained, so the
  negative-signal history accumulates without ever violating the invariant.
* **Organization scoping is mandatory.** Every operation resolves the source
  item through an ``organization_id``-scoped lookup, so a cross-tenant or
  missing item is indistinguishable and yields ``404`` (Requirement 2.3). The
  service adds/flushes within the caller's transaction and never commits on its
  own — the route's ``get_db`` dependency commits on success and rolls back on
  error, keeping the confirm mutation and its audit row atomic (Requirement
  5.4).

Provider access is injected as a ``classifier`` callable ``(content, title) ->
ClassificationOutput`` rather than a hard dependency on a concrete provider.
This lets the classification route (task 9.2) route the call through
:func:`app.dependencies.call_with_fallback` (so the LLM→mock failure policy
applies) while the service still owns source-item resolution and persistence.
When no classifier is supplied the deterministic :class:`MockAIProvider` is used
so the service is usable and testable standalone.
"""

from __future__ import annotations

from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    ClassificationResult,
    SourceItem,
    SourceStatus,
    SuggestionStatus,
)
from app.core.schemas import ClassificationOverride
from app.core.services.ai_provider import ClassificationOutput, MockAIProvider
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select

# A classifier turns a source item's ``(content, title)`` into a structured
# :class:`ClassificationOutput`. Routes wrap the provider call in
# ``call_with_fallback`` and pass the wrapped callable here.
Classifier = Callable[[str, str], ClassificationOutput]

#: Audit action type recorded when a classification is confirmed (Requirement 5.4).
CONFIRM_CLASSIFICATION = "CONFIRM_CLASSIFICATION"


class ClassificationService:
    """Produce AI classification suggestions and apply human confirmations."""

    def __init__(self, db: Session, audit: AuditService | None = None) -> None:
        """Bind the service to a request-scoped session.

        Args:
            db: The request-scoped session. Its transaction is owned by the
                caller (the route); this service adds/flushes rows but never
                commits, so the confirm mutation and its audit row commit (or
                roll back) together atomically.
            audit: The :class:`AuditService` used to record confirmations in the
                same transaction. Defaults to a fresh instance bound to ``db``.
        """

        self.db = db
        self.audit = audit or AuditService(db)

    # -- Internal helpers ---------------------------------------------------

    def _get_item(self, org_id: UUID, item_id: UUID) -> SourceItem:
        """Resolve an org-scoped source item or raise ``404`` (Requirement 2.3)."""

        stmt = scope_select(select(SourceItem), SourceItem, org_id).where(
            SourceItem.id == item_id
        )
        item = self.db.execute(stmt).scalar_one_or_none()
        if item is None:
            raise not_found("Source item not found.")
        return item

    def _active_result(self, item_id: UUID) -> ClassificationResult | None:
        """Return the item's current non-rejected classification, if any.

        Because at most one non-rejected result exists per item (Requirement
        4.5), this returns that single active row (``SUGGESTED`` or
        ``CONFIRMED``) or ``None`` when the item has no active classification.
        """

        stmt = (
            select(ClassificationResult)
            .where(ClassificationResult.source_item_id == item_id)
            .where(ClassificationResult.status != SuggestionStatus.REJECTED)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # -- Classify -----------------------------------------------------------

    def classify(
        self,
        org_id: UUID,
        item_id: UUID,
        classifier: Classifier | None = None,
    ) -> ClassificationResult:
        """Classify a source item, persisting a ``SUGGESTED`` result.

        Resolves the org-scoped item (``404`` if missing/cross-tenant), invokes
        the AI provider via ``classifier(content, title)`` (Requirement 4.1),
        and persists a :class:`ClassificationResult` in status ``SUGGESTED`` —
        never ``CONFIRMED`` (Requirements 4.2, 5.5). The provider's bounded
        confidence and evidence-backed reasons are stored verbatim
        (Requirements 4.3, 4.4).

        To uphold "at most one non-rejected result per item" (Requirement 4.5),
        any existing active result is first rejected (retained as a negative
        signal, Requirement 5.3) before the new suggestion is written, so
        re-classifying an item replaces its active suggestion without violating
        the invariant.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The source item to classify.
            classifier: Optional ``(content, title) -> ClassificationOutput``
                callable (typically wrapped in
                :func:`app.dependencies.call_with_fallback` by the route). When
                omitted, the deterministic :class:`MockAIProvider` is used.

        Returns:
            The persisted (flushed) ``SUGGESTED`` :class:`ClassificationResult`.
        """

        item = self._get_item(org_id, item_id)

        run = classifier or MockAIProvider().classify_source_item
        output = run(item.content, item.title)

        # Uphold Requirement 4.5: retire any existing active result (retained as
        # a negative signal) so exactly one non-rejected row exists per item.
        existing = self._active_result(item.id)
        if existing is not None:
            existing.status = SuggestionStatus.REJECTED
            self.db.add(existing)
            self.db.flush()

        result = ClassificationResult(
            source_item_id=item.id,
            relevance=output.relevance,
            business_category=output.business_category,
            sensitivity=output.sensitivity,
            confidence=output.confidence,
            reasons=list(output.reasons),
            evidence_spans=[span.model_dump() for span in output.evidence_spans],
            status=SuggestionStatus.SUGGESTED,
        )
        self.db.add(result)
        self.db.flush()
        return result

    # -- Confirm ------------------------------------------------------------

    def confirm(
        self,
        org_id: UUID,
        item_id: UUID,
        actor_id: UUID,
        override: ClassificationOverride | None = None,
    ) -> ClassificationResult:
        """Confirm a suggested classification in one transaction.

        Resolves the org-scoped item (``404`` if missing/cross-tenant) and its
        current active classification (``404`` if there is nothing to confirm).
        Then, atomically in the caller's transaction (Requirement 5.4):

        * applies the optional human ``override`` — any provided axis replaces
          the AI-suggested value, any ``None`` field keeps it (Requirement 5.2);
        * sets the result status to ``CONFIRMED`` (Requirement 5.1);
        * sets the source item status to ``CLASSIFIED`` (Requirement 5.1);
        * records exactly one ``CONFIRM_CLASSIFICATION`` audit row via
          :class:`AuditService` (Requirements 5.4, 13.1).

        Args:
            org_id: The tenant the item must belong to.
            item_id: The classified source item to confirm.
            actor_id: The user confirming the classification (audit actor).
            override: Optional human edits to the classification axes.

        Returns:
            The updated (flushed) ``CONFIRMED`` :class:`ClassificationResult`.

        Raises:
            HTTPException: ``404`` if the item or an active classification does
                not exist for the organization.
        """

        item = self._get_item(org_id, item_id)
        result = self._active_result(item.id)
        if result is None:
            raise not_found("No classification to confirm.")

        # Requirement 5.2: apply the human override per axis; None keeps the AI
        # value. Record which axes the human changed for the audit detail.
        overridden: dict[str, str] = {}
        if override is not None:
            if override.relevance is not None and override.relevance != result.relevance:
                overridden["relevance"] = override.relevance.value
                result.relevance = override.relevance
            if (
                override.business_category is not None
                and override.business_category != result.business_category
            ):
                overridden["business_category"] = override.business_category.value
                result.business_category = override.business_category
            if (
                override.sensitivity is not None
                and override.sensitivity != result.sensitivity
            ):
                overridden["sensitivity"] = override.sensitivity.value
                result.sensitivity = override.sensitivity

        result.status = SuggestionStatus.CONFIRMED
        item.status = SourceStatus.CLASSIFIED
        self.db.add_all([result, item])

        # Requirement 5.4 / 13.1: exactly one audit row in this transaction.
        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=CONFIRM_CLASSIFICATION,
            target_type="SourceItem",
            target_id=item.id,
            detail={
                "classification_result_id": str(result.id),
                "overridden": overridden,
            },
        )

        self.db.flush()
        return result

    # -- Reject -------------------------------------------------------------

    def reject(
        self,
        org_id: UUID,
        item_id: UUID,
        actor_id: UUID,
    ) -> ClassificationResult:
        """Reject a suggested classification, retaining it as a negative signal.

        Resolves the org-scoped item (``404`` if missing/cross-tenant) and its
        current active classification (``404`` if there is nothing to reject),
        then sets that result's status to ``REJECTED``. The row is **retained**,
        not deleted, so it remains available as a negative signal (Requirement
        5.3). The source item status is left unchanged so the item can be
        re-classified.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The source item whose classification to reject.
            actor_id: The user rejecting the classification.

        Returns:
            The updated (flushed) ``REJECTED`` :class:`ClassificationResult`.

        Raises:
            HTTPException: ``404`` if the item or an active classification does
                not exist for the organization.
        """

        item = self._get_item(org_id, item_id)
        result = self._active_result(item.id)
        if result is None:
            raise not_found("No classification to reject.")

        result.status = SuggestionStatus.REJECTED
        self.db.add(result)
        self.db.flush()
        return result


__all__ = ["ClassificationService", "CONFIRM_CLASSIFICATION"]
