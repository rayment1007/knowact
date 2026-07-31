// LoginPage: email + password sign-in for KnowAct.
//
// Flow:
//   1. Submit credentials to the auth store's login(), which posts to
//      POST /api/auth/login (setting the HTTP-only session cookie) and then
//      loads the current user + organization from GET /api/auth/me.
//   2. Redirect to the page the user originally attempted (captured by
//      ProtectedRoute in location.state.from), defaulting to the enterprise
//      dashboard ("/").
//
// A 401 from the login endpoint means invalid credentials and is surfaced
// inline; the form never leaves the page or establishes a session in that case.
// If the user is already authenticated, they are redirected away from /login.

import { useState } from "react";
import type { FormEvent } from "react";
import {
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { ApiError, googleAuthApi } from "@/api";
import { useAuth } from "@/auth";

interface FromLocationState {
  from?: { pathname?: string };
}

const DASHBOARD_ROUTE = "/";

export default function LoginPage() {
  const { isAuthenticated, isLoading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();

  // The Google sign-in callback redirects here with ?error=google_signin_failed
  // when verification fails; seed the inline error UI with a message.
  const googleSignInFailed =
    searchParams.get("error") === "google_signin_failed";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(
    googleSignInFailed
      ? "We couldn't complete Google sign-in. Please try again."
      : null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [googleStarting, setGoogleStarting] = useState(false);

  // Where to send the user after a successful login: the page they originally
  // tried to reach, or the enterprise dashboard.
  const state = location.state as FromLocationState | null;
  const redirectTo = state?.from?.pathname ?? DASHBOARD_ROUTE;

  // Already signed in (e.g. navigated to /login directly): bounce to the app.
  if (!isLoading && isAuthenticated) {
    return <Navigate to={redirectTo} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    setError(null);
    setSubmitting(true);
    try {
      // login() sets the session cookie (via /login) and loads the session; on
      // success the auth store flips to authenticated and we enter the app.
      await login({ email, password });
      navigate(redirectTo, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Invalid email or password. Please try again.");
      } else {
        setError("Something went wrong while signing in. Please try again.");
      }
      setSubmitting(false);
    }
  }

  // Begin Google OpenID Connect sign-in (openid email profile only). Hands off
  // to Google's consent screen; the backend callback establishes the session.
  async function handleGoogleSignIn() {
    if (googleStarting) return;
    setError(null);
    setGoogleStarting(true);
    try {
      const redirect = await googleAuthApi.start();
      window.location.assign(redirect.authorization_url);
    } catch {
      setError("Could not start Google sign-in. Please try again.");
      setGoogleStarting(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold text-brand-700">
            KnowAct
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Sign in to your workspace
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
          <form className="space-y-5" onSubmit={handleSubmit} noValidate>
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-slate-700"
              >
                Email
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-50"
                placeholder="you@company.com"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-slate-700"
              >
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-50"
                placeholder="••••••••"
              />
            </div>

            {error ? (
              <div
                role="alert"
                className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
              >
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={submitting}
              className="flex w-full items-center justify-center rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
              or
            </span>
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          <button
            type="button"
            onClick={handleGoogleSignIn}
            disabled={googleStarting || submitting}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden>
              <path
                fill="#4285F4"
                d="M23.52 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.88c2.27-2.09 3.57-5.17 3.57-8.87z"
              />
              <path
                fill="#34A853"
                d="M12 24c3.24 0 5.95-1.08 7.94-2.91l-3.88-3c-1.08.72-2.45 1.16-4.06 1.16-3.13 0-5.78-2.11-6.73-4.96H1.26v3.09A12 12 0 0 0 12 24z"
              />
              <path
                fill="#FBBC05"
                d="M5.27 14.29a7.2 7.2 0 0 1 0-4.58V6.62H1.26a12 12 0 0 0 0 10.76l4.01-3.09z"
              />
              <path
                fill="#EA4335"
                d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.44-3.44A11.5 11.5 0 0 0 12 0 12 12 0 0 0 1.26 6.62l4.01 3.09C6.22 6.86 8.87 4.75 12 4.75z"
              />
            </svg>
            {googleStarting ? "Redirecting…" : "Sign in with Google"}
          </button>
        </div>

      </div>
    </div>
  );
}
