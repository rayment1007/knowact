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
import { clearApiCache } from "@/api/cache";
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

const LOADING: SessionState = {
  status: "loading",
  user: null,
  organization: null,
};

const SESSION_ERROR: SessionState = {
  status: "error",
  user: null,
  organization: null,
};

export default function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>(LOADING);
  const [restoreAttempt, setRestoreAttempt] = useState(0);

  // React to global 401s: drop the session so ProtectedRoute redirects.
  useEffect(() => {
    const handleUnauthorized = () => { clearApiCache(); setSession(UNAUTHENTICATED); };
    setUnauthorizedHandler(handleUnauthorized);
    return () => setUnauthorizedHandler(null);
  }, []);

  // Restore the session on mount by probing /api/auth/me. If the browser holds
  // a valid session cookie the call succeeds; otherwise a 401 means the user is
  // unauthenticated.
  useEffect(() => {
    let cancelled = false;

    async function restore() {
      clearApiCache();
      setSession(LOADING);
      try {
        const me = await authApi.getCurrentUser();
        if (!cancelled) {
          setSession({
            status: "authenticated",
            user: me.user,
            organization: me.organization,
          });
        }
      } catch (error) {
        if (cancelled) return;
        // Only an explicit 401 proves that there is no valid session. A
        // network/5xx failure leaves the session unknown and must be retryable.
        setSession(
          error instanceof ApiError && error.status === 401
            ? UNAUTHENTICATED
            : SESSION_ERROR,
        );
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, [restoreAttempt]);

  const retrySession = useCallback(() => {
    setRestoreAttempt((attempt) => attempt + 1);
  }, []);

  const login = useCallback(async (credentials: LoginRequest) => {
    // Posting to /login sets the HTTP-only cookie; the response body carries
    // the authenticated user. Load the organization from /me to complete the
    // session. A 401 here (bad credentials) propagates to the caller.
    await authApi.login(credentials);
    clearApiCache();
    try {
      const me = await authApi.getCurrentUser();
      setSession({
        status: "authenticated",
        user: me.user,
        organization: me.organization,
      });
    } catch (error) {
      setSession(
        error instanceof ApiError && error.status === 401
          ? UNAUTHENTICATED
          : SESSION_ERROR,
      );
      throw error;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
      clearApiCache();
      setSession(UNAUTHENTICATED);
    } catch (error) {
      // An expired session is already logged out. For network/5xx failures the
      // HTTP-only cookie may still be valid, so retain local state and let the
      // caller show a retryable error instead of claiming logout succeeded.
      if (error instanceof ApiError && error.status === 401) {
        clearApiCache();
        setSession(UNAUTHENTICATED);
        return;
      }
      throw error;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status: session.status,
      isLoading: session.status === "loading",
      isAuthenticated: session.status === "authenticated",
      retrySession,
      user: session.user,
      organization: session.organization,
      login,
      logout,
    }),
    [session, login, logout, retrySession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
