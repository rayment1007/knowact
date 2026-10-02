import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { Citation } from "@/api";
import CopilotPage, { AnswerText } from "@/pages/CopilotPage";

const citation: Citation = {
  source_type: "ACTION",
  source_id: "12a45678-1234-4123-8123-123456789abc",
  title: "Review draft",
  evidence_excerpt: "Review the draft before the meeting.",
  timestamp: null,
  deep_link: "/workspace/actions?item=12a45678-1234-4123-8123-123456789abc",
};
const json = (body: unknown) => new Response(JSON.stringify(body), {
  headers: { "Content-Type": "application/json" },
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mountChat() {
  let answer!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { answer = resolve; });
  const fetchMock = vi.fn((url: string, _options?: RequestInit) => url.endsWith("suggested-questions")
    ? Promise.resolve(json({ questions: ["What needs my attention today?"] })) : pending);
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><CopilotPage embedded /></MemoryRouter>);
  const transcript = screen.getByRole("log", { name: "Copilot conversation" });
  Object.defineProperties(transcript, {
    scrollHeight: { configurable: true, value: 1200 },
    clientHeight: { configurable: true, value: 400 },
  });
  return { transcript, fetchMock, resolve: async () => {
    await act(async () => answer(json({
      intent: "ASK", answer: "Review draft is overdue [1].", citations: [citation],
      insufficient_evidence: false, suggested_artifact: null,
    })));
  } };
}

describe("Copilot readable references and conversation", () => {
  it("renders emphasis and lists, replaces ID labels, and keeps model HTML inert", () => {
    const { container } = render(<MemoryRouter><AnswerText answer={'1. **Review draft** — Check this.\n- **ID**: [1]\n\n2. **Next item**\n<script>alert(1)</script>'} citations={[citation]} /></MemoryRouter>);
    expect(container.querySelector("strong")?.textContent).toBe("Review draft");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("ID:");
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByRole("link", { name: "Source 1: Review draft" })).toHaveAttribute("href", citation.deep_link);
  });
  it("links validated citations and hides IDs from legacy answer text", () => {
    const { container } = render(<MemoryRouter><AnswerText
      answer={`Review this (id=${citation.source_id}). Also [8].`} citations={[citation]}
    /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Source 1: Review draft" })).toHaveAttribute("href", citation.deep_link);
    expect(container.textContent).toBe("Review this [1]. Also [8].");
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("sends the local timezone and follows the new answer", async () => {
    const { transcript, fetchMock, resolve } = mountChat();
    fireEvent.click(await screen.findByRole("button", { name: "What needs my attention today?" }));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith("/ask"));
    const options = request?.[1] as RequestInit | undefined;
    expect(JSON.parse(options?.body as string).utc_offset_minutes).toBe(-new Date().getTimezoneOffset());
    expect(transcript.scrollTop).toBe(1200);
    await resolve();
    expect(screen.getByRole("link", { name: "Source 1: Review draft" })).toHaveAttribute("href", citation.deep_link);
    expect(screen.getByText("[1] ACTION")).toBeInTheDocument();
    expect(transcript.scrollTop).toBe(1200);
  });

  it("preserves the reader's scroll position when they scroll up during a request", async () => {
    const { transcript, resolve } = mountChat();
    fireEvent.click(await screen.findByRole("button", { name: "What needs my attention today?" }));
    transcript.scrollTop = 100;
    fireEvent.scroll(transcript);
    await resolve();
    expect(transcript.scrollTop).toBe(100);
  });
});
