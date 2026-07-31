"""Core Engine service layer.

Services own the Core Engine's business rules and transactions
(Collect → Classify → Connect → Summarize → Recommend → Act → Learn). Routes
delegate to services; services depend on models/DB and on the
:class:`~app.core.services.ai_provider.AIProvider` abstraction.

This package currently exposes the AI provider abstraction (Requirement 11).
The concrete services are added in later tasks.
"""
