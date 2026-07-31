// IntegrationsPage: connect and manage Google service integrations
// (Requirements 24, 25).
//
// Responsibilities:
//   - List the current user's connections via `GET /api/integrations`, showing
//     each connection's status, connected account email, granted scopes, last
//     successful sync time, and last sync error (Requirement 24.1). Responses
//     are token-safe — no token/key is ever present (Requirements 25.2, 25.3).
//   - Connect a service (Gmail / Calendar) via incremental OAuth: begin the
//     flow with `POST /api/integrations/{service}/connect` and redirect the
//     browser to Google's authorization URL (Requirement 24.2).
//   - Disconnect (`POST /api/integrations/{id}/disconnect`) to stop future sync
//     (Requirement 24.5) and revoke (`POST /api/integrations/{id}/revoke`) to
//     revoke Google access and disconnect locally (Requirement 24.6). After any
//     mutation the list is re-fetched so the UI reflects persisted server state.
//
// Loading / empty / error states reuse the shared feedback components.

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, integrationsApi } from "@/api";
import type { IntegrationConnection, IntegrationService } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";

// Human-readable service labels for the post-callback confirmation banner.
const SERVICE_LABELS: Record<string, string> = {
  GMAIL: "Gmail",
  GOOGLE_CALENDAR: "Google Calendar",
};

// The services this page can connect, in display order.
const SERVICES: { service: IntegrationService; label: string; blurb: string }[] =
  [
    {
      service: "GMAIL",
      label: "Gmail",
      blurb: "Import and triage email into your workspace (read-only).",
    },
    {
      service: "GOOGLE_CALENDAR",
      label: "Google Calendar",
      blurb: "Add confirmed actions to your calendar as events.",
    },
  ];

/** Tailwind chip classes for each connection status. */
function statusBadgeClass(status: string): string {
  switch (status) {
    case "CONNECTED":
      return "bg-emerald-50 text-emerald-700";
    case "EXPIRED":
      return "bg-amber-50 text-amber-700";
    case "ERROR":
      return "bg-red-50 text-red-700";
    case "REVOKED":
    default:
      return "bg-slate-100 text-slate-500";
  }
}

/** Format an ISO timestamp for display; falls back to the raw value. */
function formatDateTime(iso: string | null): string {
  if (!iso) return "Never";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** Short label for a granted OAuth scope (drops the Google URL prefix). */
function scopeLabel(scope: string): string {
  const slash = scope.lastIndexOf("/");
  return slash >= 0 ? scope.slice(slash + 1) : scope;
}

function describeError(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return `That connection no longer exists. The list has been refreshed.`;
    }
    return `Could not ${verb} (${err.status}). Please retry.`;
  }
  return `Could not ${verb}. Please retry.`;
}

interface ServiceCardProps {
  label: string;
  blurb: string;
  service: IntegrationService;
  connection: IntegrationConnection | undefined;
  busy: boolean;
  onConnect: (service: IntegrationService) => void;
  onDisconnect: (connection: IntegrationConnection) => void;
  onRevoke: (connection: IntegrationConnection) => void;
}

function ServiceCard({
  label,
  blurb,
  service,
  connection,
  busy,
  onConnect,
  onDisconnect,
  onRevoke,
}: ServiceCardProps) {
  const isActive = connection && connection.status === "CONNECTED";

  return (
    <li className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">{label}</span>
            {connection ? (
              <span
                className={`rounded px-2 py-0.5 text-[11px] font-medium ${statusBadgeClass(
                  connection.status,
                )}`}
              >
                {connection.status}
              </span>
            ) : (
              <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">
                Not connected
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-600">{blurb}</p>

          {connection ? (
            <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs text-slate-500 sm:grid-cols-2">
              <div>
                <dt className="inline font-medium text-slate-600">Account: </dt>
                <dd className="inline">{connection.account_email}</dd>
              </div>
              <div>
                <dt className="inline font-medium text-slate-600">
                  Last sync:{" "}
                </dt>
                <dd className="inline">
                  {formatDateTime(connection.last_sync_at)}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="inline font-medium text-slate-600">Scopes: </dt>
                <dd className="inline">
                  {connection.granted_scopes.length > 0
                    ? connection.granted_scopes.map(scopeLabel).join(", ")
                    : "—"}
                </dd>
              </div>
              {connection.last_error ? (
                <div className="sm:col-span-2">
                  <dt className="inline font-medium text-red-600">
                    Last error:{" "}
                  </dt>
                  <dd className="inline text-red-600">
                    {connection.last_error}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col gap-2">
          {isActive ? (
            <>
              <button
                type="button"
                onClick={() => onDisconnect(connection!)}
                disabled={busy}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Disconnect
              </button>
              <button
                type="button"
                onClick={() => onRevoke(connection!)}
                disabled={busy}
                className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Revoke access
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => onConnect(service)}
              disabled={busy}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {connection ? "Reconnect" : "Connect"}
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export default function IntegrationsPage() {
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyService, setBusyService] = useState<string | null>(null);

  // The OAuth callback redirects the browser back here with either
  // ?connected={service} (success) or ?error=connect_failed (failure).
  const [searchParams, setSearchParams] = useSearchParams();
  const connected = searchParams.get("connected");
  const callbackError = searchParams.get("error");
  const notice = connected
    ? `${SERVICE_LABELS[connected] ?? connected} connected.`
    : null;
  const banner = callbackError
    ? "Could not connect that service. Please try again."
    : null;

  // Clear the one-time callback params from the URL after reading them so a
  // refresh doesn't re-show the banner.
  useEffect(() => {
    if (connected || callbackError) {
      const next = new URLSearchParams(searchParams);
      next.delete("connected");
      next.delete("error");
      setSearchParams(next, { replace: true });
    }
    // Only run when the callback params change.
  }, [connected, callbackError, searchParams, setSearchParams]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await integrationsApi.list();
      setConnections(result);
    } catch {
      setError("Could not load your integrations. Please retry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byService = new Map<string, IntegrationConnection>();
  for (const connection of connections) {
    // Prefer an active connection when several exist for a service.
    const existing = byService.get(connection.service);
    if (!existing || connection.status === "CONNECTED") {
      byService.set(connection.service, connection);
    }
  }

  const handleConnect = useCallback(async (service: IntegrationService) => {
    setBusyService(service);
    setActionError(null);
    try {
      const redirect = await integrationsApi.connect(service);
      // Hand off to Google's consent screen (incremental OAuth).
      window.location.assign(redirect.authorization_url);
    } catch (err) {
      setActionError(describeError(err, "start authorization"));
      setBusyService(null);
    }
  }, []);

  const handleDisconnect = useCallback(
    async (connection: IntegrationConnection) => {
      setBusyService(connection.service);
      setActionError(null);
      try {
        await integrationsApi.disconnect(connection.id);
      } catch (err) {
        setActionError(describeError(err, "disconnect"));
      } finally {
        setBusyService(null);
        await load();
      }
    },
    [load],
  );

  const handleRevoke = useCallback(
    async (connection: IntegrationConnection) => {
      setBusyService(connection.service);
      setActionError(null);
      try {
        await integrationsApi.revoke(connection.id);
      } catch (err) {
        setActionError(describeError(err, "revoke access"));
      } finally {
        setBusyService(null);
        await load();
      }
    },
    [load],
  );

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">Integrations</h1>
        <p className="mt-1 text-sm text-slate-500">
          Connect Google services to bring email and calendar into your
          workspace. You control what the platform can access, and you can
          disconnect or revoke access at any time.
        </p>
      </header>

      {notice ? (
        <div
          role="status"
          className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700"
        >
          {notice}
        </div>
      ) : null}

      {banner ? (
        <div className="mb-4">
          <ErrorState message={banner} variant="alert" />
        </div>
      ) : null}

      {actionError ? (
        <div className="mb-4">
          <ErrorState message={actionError} variant="alert" />
        </div>
      ) : null}

      {loading ? (
        <LoadingState variant="skeleton" rows={2} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <ul className="space-y-4">
          {SERVICES.map(({ service, label, blurb }) => (
            <ServiceCard
              key={service}
              service={service}
              label={label}
              blurb={blurb}
              connection={byService.get(service)}
              busy={busyService === service}
              onConnect={handleConnect}
              onDisconnect={handleDisconnect}
              onRevoke={handleRevoke}
            />
          ))}
        </ul>
      )}

      {!loading && !error && connections.length === 0 ? (
        <div className="mt-6">
          <EmptyState
            variant="plain"
            title="No integrations connected yet."
            description="Connect Gmail or Google Calendar above to get started."
          />
        </div>
      ) : null}
    </div>
  );
}
