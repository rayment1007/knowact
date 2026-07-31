// Typed API wrappers for Connected Workspace Intelligence integrations and
// Google Sign-In (Requirements 23, 24, 25).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization and user. Responses are token-safe:
// no token or key field is ever present (Requirements 25.2, 25.3), which is
// reflected in the `IntegrationConnection` type having no token fields.

import { api } from "./client";
import type {
  AuthorizationRedirect,
  IntegrationConnection,
  IntegrationService,
} from "./types";

export const integrationsApi = {
  /**
   * List the current user's connections for their organization (Requirement
   * 24.1): status, account email, granted scopes, last sync time, last error.
   */
  list: () => api.get<IntegrationConnection[]>("/integrations"),

  /**
   * Begin incremental authorization for a service (Requirement 24.2). Returns
   * the Google authorization URL and opaque state; the caller redirects the
   * browser to `authorization_url` to grant the single capability scope.
   */
  connect: (service: IntegrationService) =>
    api.post<AuthorizationRedirect>(`/integrations/${service}/connect`),

  // Completing an incremental authorization (Requirement 24.3) is NOT an XHR:
  // Google redirects the browser to the backend callback
  // (GET /api/integrations/callback), which persists the connection and then
  // issues a 302 redirect back to this SPA at
  // `/integrations?connected={service}` (or `?error=connect_failed`). The
  // IntegrationsPage reads those query params to show a confirmation banner and
  // re-fetches the list — so there is no client-side callback call to make.

  /**
   * Disconnect a connection (Requirement 24.5): sets status REVOKED and stops
   * all future sync. Existing imported data is retained. Throws ApiError(404)
   * for an unknown or cross-tenant id.
   */
  disconnect: (connectionId: string) =>
    api.post<IntegrationConnection>(`/integrations/${connectionId}/disconnect`),

  /**
   * Revoke Google access for a connection and disconnect it locally
   * (Requirement 24.6). Throws ApiError(404) for an unknown or cross-tenant id.
   */
  revoke: (connectionId: string) =>
    api.post<IntegrationConnection>(`/integrations/${connectionId}/revoke`),
};

export const googleAuthApi = {
  /**
   * Begin Google OIDC sign-in (openid email profile only). Returns the Google
   * authorization URL and state; the caller redirects the browser there. A
   * 401 is handled inline, so this opts out of the global auth redirect.
   */
  start: () =>
    api.get<AuthorizationRedirect>("/auth/google/start", {
      skipAuthRedirect: true,
    }),
};
