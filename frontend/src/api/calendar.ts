// Typed API wrappers for Connected Workspace Intelligence Calendar event
// creation from confirmed actions (M6.3, Requirement 28).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization and user. Responses are token-safe.
// Cross-org connection / link / action ids yield ApiError(404); adding a
// non-confirmed action yields ApiError(409). "Add to Calendar" is idempotent:
// repeatedly adding the same confirmed action to the same calendar returns the
// same link and creates at most one Google event (Requirement 28.7).

import { api } from "./client";
import type {
  CalendarAddRequest,
  CalendarDailyBriefBlockRequest,
  CalendarEventLink,
  CalendarUpdateRequest,
  CalendarView,
} from "./types";

export const calendarApi = {
  /** List the writable calendars for a connection (Requirement 28.3). */
  listCalendars: (connectionId: string) =>
    api.get<CalendarView[]>(`/calendar/${connectionId}/calendars`),

  /**
   * Add a confirmed action to Google Calendar (Requirements 28.1-28.8).
   * Idempotent per (action, calendar). Throws ApiError(409) for a
   * non-confirmed action, ApiError(404) for a cross-org/missing id.
   */
  addActionToCalendar: (actionId: string, request: CalendarAddRequest) =>
    api.post<CalendarEventLink>(
      `/actions/${actionId}/add-to-calendar`,
      request,
    ),

  /** List the current user's calendar event links with sync state (28.6). */
  listLinks: () => api.get<CalendarEventLink[]>("/calendar/links"),

  /** Fetch one organization-scoped calendar link for a direct detail route. */
  getLink: (linkId: string) =>
    api.get<CalendarEventLink>(`/calendar/links/${linkId}`),

  /** Update an existing calendar event in place (Requirement 28.6). */
  updateEvent: (linkId: string, request: CalendarUpdateRequest) =>
    api.patch<CalendarEventLink>(`/calendar/links/${linkId}`, request),

  /** Cancel/delete a calendar event on Google (Requirement 28.6). */
  cancelEvent: (linkId: string) =>
    api.post<CalendarEventLink>(`/calendar/links/${linkId}/cancel`),

  /** Retry a failed calendar sync (Requirement 28.6). */
  retry: (linkId: string) =>
    api.post<CalendarEventLink>(`/calendar/links/${linkId}/retry`),

  /** Create the optional recurring Daily Brief calendar block (Req 28.9). */
  createDailyBriefBlock: (request: CalendarDailyBriefBlockRequest) =>
    api.post<CalendarEventLink>("/calendar/daily-brief-block", request),
};
