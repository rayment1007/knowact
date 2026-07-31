// Typed API wrappers for the authentication endpoints.
//
// Authentication is cookie-based (the backend sets an HTTP-only JWT cookie).
// The `login` and `getCurrentUser` calls opt out of the global 401 redirect
// (`skipAuthRedirect`) because they handle authentication failures themselves:
//   - login: a 401 means "invalid credentials" and is surfaced inline.
//   - getCurrentUser: a 401 during session restore means there is no valid
//     session cookie; the auth store treats the user as unauthenticated.

import { api } from "./client";
import type {
  CurrentUserResponse,
  LoginRequest,
  LoginResponse,
} from "./types";

export const authApi = {
  /** Authenticate with email + password. Throws ApiError(401) on bad creds. */
  login: (credentials: LoginRequest) =>
    api.post<LoginResponse>("/auth/login", credentials, {
      skipAuthRedirect: true,
    }),

  /** Fetch the current user + organization for the active session cookie. */
  getCurrentUser: () =>
    api.get<CurrentUserResponse>("/auth/me", { skipAuthRedirect: true }),

  /** Clear the session by asking the server to delete the auth cookie. */
  logout: () => api.post<void>("/auth/logout"),
};
