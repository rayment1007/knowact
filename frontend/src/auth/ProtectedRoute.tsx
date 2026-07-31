// ProtectedRoute: gate for authenticated-only routes.
//
// - While the session is being restored, render a minimal loading state.
// - When unauthenticated, redirect to /login, preserving the attempted
//   location so the login flow can send the user back afterwards.
// - When authenticated, render the nested routes (<Outlet/>).

import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";

export default function ProtectedRoute() {
  const { isLoading, isAuthenticated } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-full items-center justify-center p-8 text-sm text-slate-500">
        Loading…
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
