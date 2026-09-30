import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import CopilotPage from "@/pages/CopilotPage";
import WorkspaceIcon from "./WorkspaceIcon";

export default function FloatingAssistant() {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (location.pathname === "/copilot") { setMounted(true); setOpen(true); navigate("/", { replace: true }); }
  }, [location.pathname, navigate]);
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return <>
    {mounted && <section ref={panel} hidden={!open} role="dialog" aria-label="KnowAct Copilot" className="fixed bottom-24 right-4 z-40 flex h-[min(620px,calc(100dvh-130px))] w-[min(440px,calc(100vw-32px))] flex-col overflow-hidden rounded-2xl border border-blue-100 bg-white shadow-2xl" style={!open ? { display: "none" } : undefined}>
      <header className="flex shrink-0 items-center justify-between border-b border-slate-100 bg-blue-50/60 px-5 py-4"><div className="flex items-center gap-2 text-sm font-semibold text-blue-800"><WorkspaceIcon name="assistant" />KnowAct Copilot</div><button onClick={() => { setOpen(false); trigger.current?.focus(); }} aria-label="Close assistant" className="rounded-lg p-2 text-slate-500 hover:bg-blue-100"><WorkspaceIcon name="close" className="h-4 w-4" /></button></header>
      <div className="min-h-0 flex-1"><CopilotPage embedded /></div>
    </section>}
    <button ref={trigger} aria-label={open ? "Hide assistant" : "Open assistant"} aria-expanded={open} onClick={() => { setMounted(true); setOpen(value => !value); }} className="fixed bottom-6 right-5 z-40 inline-flex h-12 items-center gap-2 rounded-full bg-blue-600 px-5 text-sm font-semibold text-white shadow-lg shadow-blue-900/20 transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200"><WorkspaceIcon name={open ? "close" : "assistant"} /><span>Copilot</span></button>
  </>;
}
