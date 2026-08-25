// ProtectedRoute: gate for authenticated-only routes.
//
// - While the session is being restored, render a minimal loading state.
// - When unauthenticated, redirect to /login, preserving the attempted
//   location so the login flow can send the user back afterwards.
// - When authenticated, render the nested routes (<Outlet/>).

import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";

export default function ProtectedRoute() {
  const { status, isLoading, isAuthenticated, retrySession } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-full items-center justify-center p-8 text-sm text-slate-500">
        Loading…
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex min-h-full items-center justify-center bg-slate-50 p-8">
        <div
          role="alert"
          className="w-full max-w-md rounded-lg border border-red-200 bg-white p-5 text-center shadow-sm"
        >
          <p className="text-sm text-red-700">
            We could not check your session. Please retry when the service is
            available.
          </p>
          <button
            type="button"
            onClick={retrySession}
            className="mt-4 rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
