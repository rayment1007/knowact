// AppShell: the authenticated application chrome.
//
// Layout:
//   - Left sidebar with grouped NavLink navigation (Workspace + Connected
//     Workspace). Active links are highlighted via NavLink's isActive state.
//   - Topbar showing the current organization and user (from useAuth) plus a
//     logout action.
//   - Main content area rendering the matched child route via <Outlet />.
//   - A footer slot carrying the operational-support disclaimer (Req 12.3),
//     present on every authenticated page.

import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth";
import OperationalSupportDisclaimer from "@/components/OperationalSupportDisclaimer";

interface NavItem {
  to: string;
  label: string;
  /** Only match on the exact path (used for index routes like "/"). */
  end?: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "Workspace",
    items: [
      { to: "/", label: "Dashboard", end: true },
      { to: "/source-inbox", label: "Source Inbox" },
      { to: "/knowledge", label: "Knowledge Hub" },
      { to: "/actions", label: "Action Center" },
      { to: "/decisions", label: "Decision Memory" },
    ],
  },
  {
    title: "Connected Workspace",
    items: [
      { to: "/copilot", label: "Copilot" },
      { to: "/gmail", label: "Gmail Sync" },
      { to: "/email-drafts", label: "Gmail AI Drafts" },
      { to: "/documents", label: "Documents" },
      { to: "/integrations", label: "Integrations" },
      { to: "/privacy", label: "Privacy & Data" },
    ],
  },
];

function navLinkClass({ isActive }: { isActive: boolean }): string {
  const base =
    "block rounded-md px-3 py-2 text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-brand-50 text-brand-700`
    : `${base} text-slate-600 hover:bg-slate-100 hover:text-slate-900`;
}

/** Derive up-to-two-letter initials from a full name or email. */
function initials(nameOrEmail: string): string {
  const source = nameOrEmail.trim();
  if (!source) return "?";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

export default function AppShell() {
  const { user, organization, logout } = useAuth();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
      navigate("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  }

  const displayName = user?.full_name || user?.email || "Signed-in user";

  return (
    <div className="flex min-h-full bg-slate-50">
      {/* Sidebar */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-5">
          <div className="text-base font-semibold text-brand-700">KnowAct</div>
          {organization ? (
            <div className="mt-1 truncate text-xs text-slate-500">
              {organization.name}
            </div>
          ) : null}
        </div>

        <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {NAV_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                {group.title}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={navLinkClass}
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Topbar */}
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-slate-900">
              {organization?.name ?? "Workspace"}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">
                {initials(displayName)}
              </div>
              <div className="hidden text-right sm:block">
                <div className="text-sm font-medium leading-tight text-slate-900">
                  {displayName}
                </div>
                {user?.email ? (
                  <div className="text-xs leading-tight text-slate-500">
                    {user.email}
                  </div>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              disabled={loggingOut}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loggingOut ? "Signing out…" : "Log out"}
            </button>
          </div>
        </header>

        {/* Routed content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>

        {/* Operational-support disclaimer slot (Requirement 12.3) */}
        <footer className="shrink-0 border-t border-slate-200 bg-white px-6 py-3">
          <OperationalSupportDisclaimer variant="footnote" />
        </footer>
      </div>
    </div>
  );
}
