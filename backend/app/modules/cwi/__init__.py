"""Connected Workspace Intelligence (CWI) module.

An additive extension (Milestone M6+) that plugs new collectors and grounded
readers into the *existing* Core Engine pipeline: Google Sign-In (OpenID
Connect), Gmail/Calendar integrations, secure token storage, document
retrieval, and an email copilot. Every CWI model carries ``organization_id``
and is scoped by the same service-layer helpers as the Core Engine, preserving
tenant isolation, the human-in-the-loop confirmation model, the privacy /
sensitivity gates, and one-audit-row-per-mutation-in-the-same-transaction.

Phase M6.1 (this milestone) adds identity + Google connection:
:class:`~app.modules.cwi.models.IntegrationConnection`, the ``TokenVault``, and
the ``IntegrationService``.
"""
