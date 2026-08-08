export interface WorkspaceNavPage {
  label: string;
  to: string;
  /** Additional route roots that should activate this page's tab. */
  routeAliases?: readonly string[];
}

export interface WorkspaceNavGroup {
  label: string;
  pages: readonly WorkspaceNavPage[];
  /** Dashboard and Actions do not need a redundant one-item tab bar. */
  showContextTabs: boolean;
}

/**
 * The single source of truth for workspace navigation.
 *
 * The first page in each group is its sidebar destination. Additional route
 * aliases keep future detail views associated with the correct group and tab.
 */
export const NAV_GROUPS = [
  {
    label: "Dashboard",
    showContextTabs: false,
    pages: [{ label: "Dashboard", to: "/" }],
  },
  {
    label: "Sources",
    showContextTabs: true,
    pages: [
      { label: "Inbox", to: "/source-inbox" },
      { label: "Gmail", to: "/gmail", routeAliases: ["/emails"] },
      { label: "Files", to: "/documents" },
    ],
  },
  {
    label: "Knowledge",
    showContextTabs: true,
    pages: [
      { label: "Hub", to: "/knowledge" },
      { label: "Decisions", to: "/decisions" },
      { label: "Verification", to: "/verification" },
    ],
  },
  {
    label: "Actions",
    showContextTabs: false,
    pages: [
      {
        label: "Action Center",
        to: "/actions",
        routeAliases: ["/calendar"],
      },
    ],
  },
  {
    label: "Copilot",
    showContextTabs: true,
    pages: [
      { label: "Assistant", to: "/copilot" },
      { label: "Email Drafts", to: "/email-drafts" },
    ],
  },
  {
    label: "Settings",
    showContextTabs: true,
    pages: [
      { label: "Connections", to: "/integrations" },
      { label: "Privacy", to: "/privacy" },
    ],
  },
] as const satisfies readonly WorkspaceNavGroup[];

function routeRootMatches(pathname: string, routeRoot: string): boolean {
  if (routeRoot === "/") return pathname === "/";
  return pathname === routeRoot || pathname.startsWith(`${routeRoot}/`);
}

export function navPageMatches(
  pathname: string,
  page: WorkspaceNavPage,
): boolean {
  return [page.to, ...(page.routeAliases ?? [])].some((routeRoot) =>
    routeRootMatches(pathname, routeRoot),
  );
}

export function findActiveNavGroup(pathname: string): WorkspaceNavGroup | null {
  return (
    NAV_GROUPS.find((group) =>
      group.pages.some((page) => navPageMatches(pathname, page)),
    ) ?? null
  );
}

