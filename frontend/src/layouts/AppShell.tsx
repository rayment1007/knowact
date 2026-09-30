// AppShell: authenticated application chrome and consolidated navigation.

import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth";
import WorkspaceSearch from "@/components/WorkspaceSearch";
import SyncStatusBar from "@/components/SyncStatusBar";
import FloatingAssistant from "@/components/FloatingAssistant";
import WorkspaceIcon from "@/components/WorkspaceIcon";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import OperationalSupportDisclaimer from "@/components/OperationalSupportDisclaimer";
import { ErrorState } from "@/components/feedback";
import {
  findActiveNavGroup,
  NAV_GROUPS,
  navPageMatches,
  type WorkspaceNavGroup,
} from "@/navigation";

function sidebarLinkClass(isActive: boolean): string {
  const base =
    "block rounded-lg px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2";
  return isActive
    ? `${base} bg-brand-50 text-brand-700`
    : `${base} text-slate-600 hover:bg-slate-100 hover:text-slate-900`;
}

function contextTabClass(isActive: boolean): string {
  const base =
    "inline-flex min-h-11 items-center border-b-2 px-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2";
  return isActive
    ? `${base} border-brand-600 text-brand-700`
    : `${base} border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800`;
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

interface WorkspaceNavigationProps {
  activeGroup: WorkspaceNavGroup | null;
  onNavigate?: () => void;
}

function WorkspaceNavigation({
  activeGroup,
  onNavigate,
}: WorkspaceNavigationProps) {
  return (
    <nav aria-label="Workspace" className="flex-1 overflow-y-auto px-3 py-5">
      <div className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Personal
      </div>

      <div className="space-y-1">
        {NAV_GROUPS.map((group) => {
          const isActive = activeGroup?.label === group.label;
          const destination = group.pages[0].to;

          return (
            <Link
              key={group.label}
              to={destination}
              aria-current={isActive ? "page" : undefined}
              className={sidebarLinkClass(isActive)}
              onClick={onNavigate}
            >
              <span className="flex items-center gap-2.5"><WorkspaceIcon name={group.label === "Dashboard" ? "home" : "workspace"} className="h-4 w-4" />{group.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

interface ContextTabsProps {
  group: WorkspaceNavGroup;
  pathname: string;
}

function ContextTabs({ group, pathname }: ContextTabsProps) {
  if (!group.showContextTabs || group.pages.length < 2) return null;

  return (
    <div className="shrink-0 border-b border-slate-200 bg-white">
      <nav
        aria-label={`${group.label} pages`}
        className="overflow-x-auto px-4 sm:px-6"
      >
        <div className="flex min-w-max gap-6">
          {group.pages.map((page) => {
            const isActive = navPageMatches(pathname, page);
            return (
              <NavLink
                key={page.to}
                to={page.to}
                aria-current={isActive ? "page" : undefined}
                className={contextTabClass(isActive)}
              >
                {page.label}
              </NavLink>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

export default function AppShell() {
  return <WorkspaceProvider><WorkspaceShell /></WorkspaceProvider>;
}

function WorkspaceShell() {
  const { user, organization, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mobileDialogRef = useRef<HTMLDivElement>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const activeGroup = findActiveNavGroup(location.pathname);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
        menuButtonRef.current?.focus();
        return;
      }
      if (event.key === "Tab") {
        const focusable = Array.from(
          mobileDialogRef.current?.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ) ?? [],
        ).filter((element) => element.getClientRects().length > 0);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [mobileNavOpen]);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch {
      setLogoutError(
        "Could not log out because the service did not confirm the request. Please retry.",
      );
    } finally {
      setLoggingOut(false);
    }
  }

  function closeMobileNavigation(returnFocus = false) {
    setMobileNavOpen(false);
    if (returnFocus) menuButtonRef.current?.focus();
  }

  const displayName = user?.full_name || user?.email || "Signed-in user";

  const account = <div className="mt-auto border-t border-slate-100 p-4" aria-label="Signed-in account">
    <div className="flex min-w-0 items-center gap-2.5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">{initials(displayName)}</div>
      <div className="min-w-0"><p className="truncate text-sm font-medium text-slate-800" title={displayName}>{displayName}</p><p className="truncate text-[11px] text-slate-500" title={user?.email}>{user?.email}</p></div>
    </div>
    <button type="button" onClick={handleLogout} disabled={loggingOut} className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50">{loggingOut ? "Signing out…" : "Log out"}</button>
  </div>;

  return (
    <div className="flex min-h-screen bg-slate-50">
      {/* Desktop sidebar */}
      <aside aria-label="Main sidebar" className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="border-b border-slate-200 px-5 py-5">
          <div className="text-base font-semibold text-brand-700">KnowAct</div>
          {organization ? (
            <div className="mt-1 truncate text-xs text-slate-500">
              {organization.name}
            </div>
          ) : null}
        </div>
        <WorkspaceNavigation activeGroup={activeGroup} />
        {account}
      </aside>

      {/* Mobile navigation drawer */}
      {mobileNavOpen ? (
        <div
          ref={mobileDialogRef}
          className="fixed inset-0 z-50 md:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Workspace navigation"
        >
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-slate-950/40"
            onClick={() => closeMobileNavigation(true)}
          />
          <aside
            id="mobile-workspace-navigation"
            className="relative flex h-full w-[min(20rem,85vw)] flex-col bg-white shadow-xl"
          >
            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
              <div className="min-w-0">
                <div className="text-base font-semibold text-brand-700">KnowAct</div>
                {organization ? (
                  <div className="mt-1 truncate text-xs text-slate-500">
                    {organization.name}
                  </div>
                ) : null}
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close navigation"
                className="ml-3 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                onClick={() => closeMobileNavigation(true)}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  className="h-5 w-5"
                >
                  <path
                    d="m6 6 12 12M18 6 6 18"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
            <WorkspaceNavigation
              activeGroup={activeGroup}
              onNavigate={() => closeMobileNavigation()}
            />
            {account}
          </aside>
        </div>
      ) : null}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
          <button ref={menuButtonRef} type="button" aria-label="Open navigation" aria-expanded={mobileNavOpen} aria-controls="mobile-workspace-navigation" className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-blue-500 md:hidden" onClick={() => setMobileNavOpen(true)}>
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" className="h-5 w-5"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
          <div className="min-w-0 flex-1"><WorkspaceSearch /></div>
          <SyncStatusBar />
          <Link to="/settings" aria-label="Settings" title="Settings" className={`shrink-0 rounded-lg p-2 transition hover:bg-blue-50 focus-visible:ring-2 focus-visible:ring-blue-500 ${location.pathname === "/settings" ? "bg-blue-50 text-blue-700" : "text-slate-500"}`}><WorkspaceIcon name="settings" /></Link>
        </header>

        {logoutError ? (
          <div className="shrink-0 px-4 pt-3 sm:px-6">
            <ErrorState message={logoutError} variant="alert" />
          </div>
        ) : null}

        <ContextTabs group={activeGroup ?? NAV_GROUPS[0]} pathname={location.pathname} />

        <main className="min-h-0 flex-1 pb-20">
          <Outlet />
        </main>

        <FloatingAssistant />
        <footer className="shrink-0 border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
          <OperationalSupportDisclaimer variant="footnote" />
        </footer>
      </div>
    </div>
  );
}
