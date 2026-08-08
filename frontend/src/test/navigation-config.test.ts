import { describe, expect, it } from "vitest";
import { findActiveNavGroup, NAV_GROUPS, navPageMatches } from "@/navigation";

describe("workspace navigation configuration", () => {
  it("renders exactly the six consolidated sidebar purposes", () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual([
      "Dashboard",
      "Sources",
      "Knowledge",
      "Actions",
      "Copilot",
      "Settings",
    ]);
  });

  it("uses the first page as each group destination", () => {
    expect(NAV_GROUPS.map((group) => group.pages[0].to)).toEqual([
      "/",
      "/source-inbox",
      "/knowledge",
      "/actions",
      "/copilot",
      "/integrations",
    ]);
  });

  it.each([
    ["/", "Dashboard"],
    ["/source-inbox", "Sources"],
    ["/gmail", "Sources"],
    ["/emails/email-1", "Sources"],
    ["/documents/document-1", "Sources"],
    ["/knowledge/item-1", "Knowledge"],
    ["/decisions/decision-1", "Knowledge"],
    ["/verification", "Knowledge"],
    ["/actions/action-1", "Actions"],
    ["/calendar/calendar-1", "Actions"],
    ["/copilot", "Copilot"],
    ["/email-drafts", "Copilot"],
    ["/integrations", "Settings"],
    ["/privacy", "Settings"],
  ])("associates %s with %s", (pathname, groupLabel) => {
    expect(findActiveNavGroup(pathname)?.label).toBe(groupLabel);
  });

  it("does not treat an unknown path as a navigation page", () => {
    expect(findActiveNavGroup("/not-a-route")).toBeNull();
  });

  it("matches Gmail detail routes to the Gmail tab", () => {
    const sources = NAV_GROUPS.find((group) => group.label === "Sources");
    const gmail = sources?.pages.find((page) => page.label === "Gmail");

    expect(gmail).toBeDefined();
    expect(navPageMatches("/emails/email-1", gmail!)).toBe(true);
  });
});
