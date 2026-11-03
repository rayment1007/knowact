import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DraftDetail } from "@/pages/EmailDraftsPage";
import type { EmailDraft } from "@/api";

afterEach(cleanup);

const saved: EmailDraft = {
  id: "draft-1", organization_id: "org-1", user_id: "owner-1",
  integration_connection_id: "connection-1", source_item_id: null, business_entity_id: null,
  gmail_thread_id: null, to_recipients: ["reviewed@example.com"], subject: "Reviewed subject",
  body_text: "Reviewed body", tone: "professional", purpose: "follow up", referenced_facts: [],
  warnings: [], status: "GMAIL_DRAFT_CREATED", gmail_draft_id: "provider-draft-1",
  gmail_sent_message_id: null, created_at: "2026-11-02T09:00:00Z", updated_at: "2026-11-02T09:00:00Z",
};
function props(draft = saved) {
  return { draft, busy: false, actionError: null, recipientError: null,
    onEdit: vi.fn(), onSaveRecipients: vi.fn(), onApprove: vi.fn(), onReject: vi.fn(),
    onCreateGmailDraft: vi.fn(), onSend: vi.fn(), onDelete: vi.fn() };
}

it("requires saved, reapproved content after editing a materialized Gmail draft", async () => {
  const user = userEvent.setup();
  const handlers = props();
  const view = render(<DraftDetail {...handlers} />);
  await user.type(screen.getByLabelText("Body"), " revised");
  expect(screen.getByRole("button", { name: "Send…" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Save edits" }));
  expect(handlers.onEdit).toHaveBeenCalledWith(saved, saved.subject, "Reviewed body revised");
  const edited = { ...saved, body_text: "Reviewed body revised", status: "AI_SUGGESTED" as const };
  view.rerender(<DraftDetail {...handlers} draft={edited} />);
  expect(screen.getByRole("button", { name: "Send…" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(handlers.onApprove).toHaveBeenCalledWith(edited);
  const approved = { ...edited, status: "USER_APPROVED" as const };
  view.rerender(<DraftDetail {...handlers} draft={approved} />);
  await user.click(screen.getByRole("button", { name: "Send…" }));
  expect(handlers.onSend).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Confirm & send" }));
  expect(handlers.onSend).toHaveBeenCalledWith(approved);
});

it("invalidates an open send confirmation when the recipient is edited", async () => {
  const user = userEvent.setup();
  const handlers = props();
  render(<DraftDetail {...handlers} />);
  await user.click(screen.getByRole("button", { name: "Send…" }));
  expect(screen.getByRole("button", { name: "Confirm & send" })).toBeEnabled();
  await user.type(screen.getByLabelText("To (recipient)"), ".wrong");
  expect(screen.queryByRole("button", { name: "Confirm & send" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send…" })).toBeDisabled();
  expect(handlers.onSend).not.toHaveBeenCalled();
});

it("does not approve or materialize content that differs from the saved preview", async () => {
  const user = userEvent.setup();
  const handlers = props({ ...saved, status: "AI_SUGGESTED" });
  const view = render(<DraftDetail {...handlers} />);
  await user.type(screen.getByLabelText("Subject"), " unsaved");
  expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  view.rerender(<DraftDetail {...handlers} draft={{ ...saved, status: "USER_APPROVED" }} />);
  expect(screen.getByRole("button", { name: "Update Gmail draft" })).toBeDisabled();
  expect(handlers.onApprove).not.toHaveBeenCalled();
});
