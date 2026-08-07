// AppShell: the authenticated application chrome.
//
// Layout:
//   - Left sidebar with a compact set of primary links and expandable workflow
//     sections. Active links are highlighted via NavLink's isActive state.
//   - Topbar showing the current organization and user (from useAuth) plus a
//     logout action.
//   - Main content area rendering the matched child route via <Outlet />.
//   - A footer slot carrying the operational-support disclaimer (Req 12.3),
//     present on every authenticated page.

import { useEffect, useState } from "react";
import {
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useAuth } from "@/auth";
import OperationalSupportDisclaimer from "@/components/OperationalSupportDisclaimer";

interface NavItem {
  to: string;
  label: string;
  /** Only match on the exact path (used for index routes like "/"). */
  end?: boolean;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

const PRIMARY_LINKS: NavItem[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/copilot", label: "Copilot" },
];

const NAV_SECTIONS: NavSection[] = [
  {
    label: "Sources",
    items: [
      { to: "/source-inbox", label: "Inbox" },
      { to: "/gmail", label: "Gmail Sync" },
      { to: "/documents", label: "Documents" },
    ],
  },
  {
    label: "Knowledge",
    items: [
      { to: "/knowledge", label: "Knowledge Hub" },
      { to: "/decisions", label: "Decision Memory" },
    ],
  },
  {
    label: "Actions",
    items: [
      { to: "/actions", label: "Action Center" },
      { to: "/email-drafts", label: "Gmail Drafts" },
    ],
  },
  {
    label: "Settings",
    items: [
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

function childNavLinkClass({ isActive }: { isActive: boolean }): string {
  const base =
    "block rounded-md px-3 py-2 text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-brand-50 text-brand-700`
    : `${base} text-slate-500 hover:bg-slate-100 hover:text-slate-900`;
}

function sectionButtonClass(isActive: boolean): string {
  const base =
    "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-brand-50 text-brand-700`
    : `${base} text-slate-600 hover:bg-slate-100 hover:text-slate-900`;
}

function pathMatches(pathname: string, item: NavItem): boolean {
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
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
  const location = useLocation();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);
  const activeSection =
    NAV_SECTIONS.find((section) =>
      section.items.some((item) => pathMatches(location.pathname, item)),
    )?.label ?? null;
  const [openSection, setOpenSection] = useState<string | null>(activeSection);

  useEffect(() => {
    if (activeSection) setOpenSection(activeSection);
  }, [activeSection]);

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

        <nav className="flex-1 overflow-y-auto px-3 py-5">
          <div className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Workspace
          </div>

          <div className="space-y-1">
            {PRIMARY_LINKS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={navLinkClass}
              >
                {item.label}
              </NavLink>
            ))}

            {NAV_SECTIONS.map((section) => {
              const isOpen = openSection === section.label;
              const isActive = activeSection === section.label;
              const sectionId = `sidebar-${section.label.toLowerCase()}`;

              return (
                <div key={section.label}>
                  <button
                    type="button"
                    className={sectionButtonClass(isActive)}
                    aria-expanded={isOpen}
                    aria-controls={sectionId}
                    onClick={() =>
                      setOpenSection((current) =>
                        current === section.label ? null : section.label,
                      )
                    }
                  >
                    <span>{section.label}</span>
                    <svg
                      aria-hidden="true"
                      viewBox="0 0 20 20"
                      fill="none"
                      className={`h-4 w-4 transition-transform ${
                        isOpen ? "rotate-180" : ""
                      }`}
                    >
                      <path
                        d="m5 7.5 5 5 5-5"
                        stroke="currentColor"
                        strokeWidth="1.75"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>

                  {isOpen ? (
                    <div
                      id={sectionId}
                      className="ml-3 mt-1 space-y-1 border-l border-slate-200 pl-3"
                    >
                      {section.items.map((item) => (
                        <NavLink
                          key={item.to}
                          to={item.to}
                          className={childNavLinkClass}
                        >
                          {item.label}
                        </NavLink>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}

          </div>
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
