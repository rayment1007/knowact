import IntegrationsPage from "./IntegrationsPage";
import PrivacyPage from "./PrivacyPage";

export default function SettingsPage() {
  return <section className="mx-auto max-w-4xl px-4 py-7 sm:px-6">
    <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
    <p className="mt-2 text-sm text-slate-500">Manage your Google connections, permissions and stored information.</p>
    <div className="mt-7"><IntegrationsPage embedded /></div>
    <div className="mt-8 border-t border-slate-200 pt-7"><PrivacyPage embedded /></div>
  </section>;
}
