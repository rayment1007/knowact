import type { ActionItem } from "@/api/types";
import type { CalendarSource } from "@/api/workspace";

export function localDay(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function dateOnly(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return localDay(date) === value ? date : null;
}
export function formatDay(value: string) {
  const date = dateOnly(value);
  return date ? date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "Date unavailable";
}
export function formatTimestamp(value?: string | null) {
  if (!value) return null;
  // Persisted server timestamps are UTC; preserve explicit provider offsets.
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + "Z");
  if (!Number.isFinite(date.getTime())) return null;
  return date.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
export function dueLabel(value: string | null, now = new Date()) {
  if (!value || !dateOnly(value)) return "No due date";
  const today = localDay(now);
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  if (value < today) return "Overdue";
  if (value === today) return "Due today";
  if (value === localDay(tomorrow)) return "Due tomorrow";
  return "Upcoming";
}
export function orderedActions(items: ActionItem[]) {
  return items.filter(item => ["OPEN", "IN_PROGRESS"].includes(item.status)).sort((a, b) =>
    (a.due_date || "9999").localeCompare(b.due_date || "9999") || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
}
export function upcomingEvents(items: CalendarSource[], now = new Date()) {
  return items.filter(item => {
    const day = dateOnly(item.starts_at);
    if (day) return dateOnly(item.ends_at) ? item.ends_at > localDay(now) : item.starts_at >= localDay(now);
    const start = new Date(item.starts_at).getTime();
    const end = new Date(item.ends_at).getTime();
    return Number.isFinite(start) && (start >= now.getTime() || (Number.isFinite(end) && end > now.getTime()));
  }).sort((a, b) => (dateOnly(a.starts_at)?.getTime() ?? new Date(a.starts_at).getTime()) - (dateOnly(b.starts_at)?.getTime() ?? new Date(b.starts_at).getTime()) || a.id.localeCompare(b.id));
}
export function eventDate(item: CalendarSource) {
  if (dateOnly(item.starts_at)) return `${formatDay(item.starts_at)} · All day`;
  return new Date(item.starts_at).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
