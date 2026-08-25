// Auth context definition and shared types.
//
// Kept separate from the provider component so that the `useAuth` hook and the
// provider can import the context without pulling in each other, and so the
// module exports a single stable context reference.

import { createContext } from "react";
import type { LoginRequest, Organization, User } from "@/api";

/** Lifecycle of the session as known by the frontend. */
export type AuthStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "error";

export interface AuthContextValue {
  status: AuthStatus;
  /** True while the initial session-restore probe is in flight. */
  isLoading: boolean;
  /** True once the user is confirmed authenticated. */
  isAuthenticated: boolean;
  /** Retry the current-session probe after a temporary backend/network error. */
  retrySession: () => void;
  /** The current user, or null when not authenticated. */
  user: User | null;
  /** The current user's organization, or null when not authenticated. */
  organization: Organization | null;
  /**
   * Authenticate with email + password. Posts to `/api/auth/login` (which sets
   * the HTTP-only session cookie), then loads the current user + organization
   * from `GET /api/auth/me`. Throws `ApiError(401)` on invalid credentials.
   */
  login: (credentials: LoginRequest) => Promise<void>;
  /**
   * Ask the server to delete the cookie, then reset local state. Rejects when
   * the server cannot confirm logout so the UI never reports a false success.
   */
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | undefined>(
  undefined,
);
