import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "@/auth";
import AppShell from "@/layouts/AppShell";
import { useLocation } from "react-router-dom";
import SettingsPage from "@/pages/SettingsPage";
import ReviewsPage from "@/pages/ReviewsPage";
import SearchPage from "@/pages/SearchPage";
import WorkspacePage, { LegacyWorkspaceRedirect } from "@/pages/WorkspacePage";
import WorkspaceActivityPage from "@/pages/WorkspaceActivityPage";
import LoginPage from "@/pages/LoginPage";
import EnterpriseDashboardPage from "@/pages/EnterpriseDashboardPage";
import ActionCenterPage from "@/pages/ActionCenterPage";
import EmailDraftsPage from "@/pages/EmailDraftsPage";

function SettingsRedirect() {
  const location = useLocation();
  return <Navigate to={`/settings${location.search}`} replace />;
}

// Application routes. All shell routes sit behind ProtectedRoute, which
// redirects unauthenticated users to /login.
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<EnterpriseDashboardPage />} />
          <Route path="workspace" element={<Navigate to="/workspace/sources" replace />} />
          <Route path="workspace/:section" element={<WorkspacePage />} />
          <Route path="source-inbox" element={<LegacyWorkspaceRedirect />} />
          <Route path="source-inbox/:sourceId" element={<LegacyWorkspaceRedirect />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="activity" element={<WorkspaceActivityPage />} />
          <Route path="reviews" element={<ReviewsPage />} />
          <Route path="calendar-sources" element={<LegacyWorkspaceRedirect />} />
          <Route path="calendar-sources/:sourceId" element={<LegacyWorkspaceRedirect />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="knowledge" element={<LegacyWorkspaceRedirect />} />
          <Route path="knowledge/:knowledgeId" element={<LegacyWorkspaceRedirect />} />
          <Route path="actions" element={<LegacyWorkspaceRedirect />} />
          <Route path="actions/:actionId" element={<LegacyWorkspaceRedirect />} />
          <Route path="decisions" element={<Navigate to="/knowledge" replace />} />
          <Route path="decisions/:decisionId" element={<Navigate to="/knowledge" replace />} />
          <Route path="integrations" element={<SettingsRedirect />} />
          <Route path="gmail" element={<LegacyWorkspaceRedirect />} />
          <Route path="emails/:emailId" element={<LegacyWorkspaceRedirect />} />
          <Route path="documents" element={<LegacyWorkspaceRedirect />} />
          <Route path="documents/:documentId" element={<LegacyWorkspaceRedirect />} />
          <Route path="calendar/:calendarLinkId" element={<ActionCenterPage />} />
          <Route path="copilot" element={<EnterpriseDashboardPage />} />
          <Route path="email-drafts" element={<EmailDraftsPage />} />
          <Route path="email-drafts/:draftId" element={<EmailDraftsPage />} />
          <Route path="privacy" element={<SettingsRedirect />} />
          <Route path="verification" element={<Navigate to="/knowledge" replace />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
