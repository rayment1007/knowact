import { describe, expect, it } from "vitest";
import type { ActionItem } from "@/api";
import type { CalendarSource } from "@/api/workspace";
import { dateOnly, dueLabel, eventDate, formatTimestamp, orderedActions, upcomingEvents } from "@/utils/workspaceDates";

describe("dashboard chronology", () => {
  const now = new Date(2026, 8, 28, 13, 21, 34);
  it("orders open work by due date and never promotes completed work", () => {
    const make = (id: string, due_date: string | null, status = "OPEN") => ({ id, due_date, status, created_at: "2026-09-01T00:00:00Z" }) as ActionItem;
    expect(orderedActions([make("undated", null), make("tomorrow", "2026-09-29"), make("overdue", "2026-09-27"), make("done", "2026-09-20", "DONE"), make("today", "2026-09-28")]).map(item => item.id)).toEqual(["overdue", "today", "tomorrow", "undated"]);
    expect(dueLabel("2026-09-28", now)).toBe("Due today");
    expect(dueLabel("2026-09-29", now)).toBe("Due tomorrow");
    expect(dueLabel("2026-09-27", now)).toBe("Overdue");
  });
  it("treats all-day dates as local dates and respects exclusive end dates", () => {
    const make = (id: string, starts_at: string, ends_at: string) => ({ id, starts_at, ends_at }) as CalendarSource;
    const ongoing = make("all-day", "2026-09-28", "2026-09-29");
    const result = upcomingEvents([ongoing, make("ended", "2026-09-27", "2026-09-28"), make("future", "2026-09-30", "2026-10-01"), make("bad", "", "")], now);
    expect(result.map(item => item.id)).toEqual(["all-day", "future"]);
    expect(eventDate(ongoing)).toBe("28 Sept 2026 · All day");
    expect(dateOnly("2026-02-31")).toBeNull();
  });
  it("shows successful timestamps down to seconds, retaining local time", () => {
    const timestamp = formatTimestamp(now.toISOString());
    expect(timestamp).toMatch(/28 Sept? 2026, 13:21:34/);
    expect(formatTimestamp("invalid")).toBeNull();
    expect(formatTimestamp(null)).toBeNull();
  });
});
