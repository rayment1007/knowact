// AppShell: authenticated application chrome and consolidated navigation.

import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth";
import OperationalSupportDisclaimer from "@/components/OperationalSupportDisclaimer";
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
        Workspace
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
              {group.label}
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
  const { user, organization, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [loggingOut, setLoggingOut] = useState(false);
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
    try {
      await logout();
      navigate("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  }

  function closeMobileNavigation(returnFocus = false) {
    setMobileNavOpen(false);
    if (returnFocus) menuButtonRef.current?.focus();
  }

  const displayName = user?.full_name || user?.email || "Signed-in user";

  return (
    <div className="flex min-h-full bg-slate-50">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="border-b border-slate-200 px-5 py-5">
          <div className="text-base font-semibold text-brand-700">KnowAct</div>
          {organization ? (
            <div className="mt-1 truncate text-xs text-slate-500">
              {organization.name}
            </div>
          ) : null}
        </div>
        <WorkspaceNavigation activeGroup={activeGroup} />
      </aside>

      {/* Mobile navigation drawer */}
      {mobileNavOpen ? (
        <div
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
          </aside>
        </div>
      ) : null}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              ref={menuButtonRef}
              type="button"
              aria-label="Open navigation"
              aria-expanded={mobileNavOpen}
              aria-controls="mobile-workspace-navigation"
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-slate-600 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
              onClick={() => setMobileNavOpen(true)}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                className="h-5 w-5"
              >
                <path
                  d="M4 7h16M4 12h16M4 17h16"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <div className="truncate text-sm font-medium text-slate-900">
              {organization?.name ?? "Workspace"}
            </div>
          </div>

          <div className="ml-3 flex shrink-0 items-center gap-3">
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
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loggingOut ? "Signing out…" : "Log out"}
            </button>
          </div>
        </header>

        <ContextTabs group={activeGroup ?? NAV_GROUPS[0]} pathname={location.pathname} />

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>

        <footer className="shrink-0 border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
          <OperationalSupportDisclaimer variant="footnote" />
        </footer>
      </div>
    </div>
  );
}
