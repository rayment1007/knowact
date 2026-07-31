// AuthProvider: owns the session state for the whole app.
//
// Authentication is cookie-based: the backend sets a signed JWT as an
// HTTP-only cookie on login, and the browser returns it automatically on every
// request (the API client uses `credentials: "include"`). The frontend never
// sees or stores the token, so this provider only tracks *derived* session
// state (the current user + organization).
//
// Responsibilities:
//   - Restore the session on load/refresh by calling GET /api/auth/me; a 401
//     (no valid cookie) yields an unauthenticated state.
//   - Expose login(credentials) — posts to /api/auth/login (which sets the
//     cookie) then loads the current user — and logout() (clears the cookie).
//   - React to 401 responses anywhere in the app by dropping to an
//     unauthenticated state (ProtectedRoute then redirects to /login).

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ApiError,
  authApi,
  setUnauthorizedHandler,
} from "@/api";
import type { LoginRequest, Organization, User } from "@/api";
import { AuthContext } from "./AuthContext";
import type { AuthContextValue, AuthStatus } from "./AuthContext";

interface SessionState {
  status: AuthStatus;
  user: User | null;
  organization: Organization | null;
}

const UNAUTHENTICATED: SessionState = {
  status: "unauthenticated",
  user: null,
  organization: null,
};

export default function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>({
    status: "loading",
    user: null,
    organization: null,
  });

  // React to global 401s: drop the session so ProtectedRoute redirects.
  useEffect(() => {
    const handleUnauthorized = () => setSession(UNAUTHENTICATED);
    setUnauthorizedHandler(handleUnauthorized);
    return () => setUnauthorizedHandler(null);
  }, []);

  // Restore the session on mount by probing /api/auth/me. If the browser holds
  // a valid session cookie the call succeeds; otherwise a 401 means the user is
  // unauthenticated.
  useEffect(() => {
    let cancelled = false;

    async function restore() {
      try {
        const me = await authApi.getCurrentUser();
        if (!cancelled) {
          setSession({
            status: "authenticated",
            user: me.user,
            organization: me.organization,
          });
        }
      } catch {
        // No valid session cookie (401) or unreachable backend.
        if (!cancelled) setSession(UNAUTHENTICATED);
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (credentials: LoginRequest) => {
    // Posting to /login sets the HTTP-only cookie; the response body carries
    // the authenticated user. Load the organization from /me to complete the
    // session. A 401 here (bad credentials) propagates to the caller.
    await authApi.login(credentials);
    try {
      const me = await authApi.getCurrentUser();
      setSession({
        status: "authenticated",
        user: me.user,
        organization: me.organization,
      });
    } catch (error) {
      setSession(UNAUTHENTICATED);
      throw error;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch (error) {
      // A 401 (already logged out / expired) is fine; anything else is
      // best-effort and should not block clearing the local session.
      if (!(error instanceof ApiError)) {
        // swallow network errors on logout
      }
    } finally {
      setSession(UNAUTHENTICATED);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status: session.status,
      isLoading: session.status === "loading",
      isAuthenticated: session.status === "authenticated",
      user: session.user,
      organization: session.organization,
      login,
      logout,
    }),
    [session, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
