import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "@/auth";
import AppShell from "@/layouts/AppShell";
import LoginPage from "@/pages/LoginPage";
import EnterpriseDashboardPage from "@/pages/EnterpriseDashboardPage";
import SourceInboxPage from "@/pages/SourceInboxPage";
import KnowledgeHubPage from "@/pages/KnowledgeHubPage";
import ActionCenterPage from "@/pages/ActionCenterPage";
import DecisionMemoryPage from "@/pages/DecisionMemoryPage";
import IntegrationsPage from "@/pages/IntegrationsPage";
import GmailSyncPage from "@/pages/GmailSyncPage";
import DocumentsPage from "@/pages/DocumentsPage";
import CopilotPage from "@/pages/CopilotPage";
import EmailDraftsPage from "@/pages/EmailDraftsPage";
import PrivacyPage from "@/pages/PrivacyPage";
import VerificationPage from "@/pages/VerificationPage";

// Application routes. All shell routes sit behind ProtectedRoute, which
// redirects unauthenticated users to /login.
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<EnterpriseDashboardPage />} />
          <Route path="source-inbox" element={<SourceInboxPage />} />
          <Route path="knowledge" element={<KnowledgeHubPage />} />
          <Route path="knowledge/:knowledgeId" element={<KnowledgeHubPage />} />
          <Route path="actions" element={<ActionCenterPage />} />
          <Route path="actions/:actionId" element={<ActionCenterPage />} />
          <Route path="decisions" element={<DecisionMemoryPage />} />
          <Route path="decisions/:decisionId" element={<DecisionMemoryPage />} />
          <Route path="integrations" element={<IntegrationsPage />} />
          <Route path="gmail" element={<GmailSyncPage />} />
          <Route path="emails/:emailId" element={<GmailSyncPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="documents/:documentId" element={<DocumentsPage />} />
          <Route path="calendar/:calendarLinkId" element={<ActionCenterPage />} />
          <Route path="copilot" element={<CopilotPage />} />
          <Route path="email-drafts" element={<EmailDraftsPage />} />
          <Route path="privacy" element={<PrivacyPage />} />
          <Route path="verification" element={<VerificationPage />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
