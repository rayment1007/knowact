import { describe, expect, it } from "vitest";
import { findActiveNavGroup, NAV_GROUPS } from "@/navigation";
describe("workspace navigation configuration", () => {
  it("has only Dashboard and Workspace in the sidebar", () => {
    expect(NAV_GROUPS.map(group => [group.label, group.pages[0].to])).toEqual([["Dashboard", "/"], ["Workspace", "/workspace/sources"]]);
    expect(NAV_GROUPS.every(group => !group.showContextTabs)).toBe(true);
  });
  it.each(["/workspace/sources", "/workspace/knowledge", "/workspace/actions", "/source-inbox", "/gmail", "/emails/id", "/documents/id", "/knowledge/id", "/actions/id", "/calendar/id", "/email-drafts"])("keeps %s within Workspace", pathname => {
    expect(findActiveNavGroup(pathname)?.label).toBe("Workspace");
  });
  it("keeps Dashboard distinct and rejects unknown roots", () => {
    expect(findActiveNavGroup("/")?.label).toBe("Dashboard");
    expect(findActiveNavGroup("/workspaces")).toBeNull();
  });
});
