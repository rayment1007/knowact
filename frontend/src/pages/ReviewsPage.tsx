import { Link } from "react-router-dom";
import { useWorkspace } from "@/components/WorkspaceProvider";
import WorkspaceIcon from "@/components/WorkspaceIcon";

export default function ReviewsPage() {
  const { summary, error, refresh } = useWorkspace();
  return <main className="mx-auto max-w-4xl px-4 py-7 sm:px-6">
    <Link to="/" className="text-sm text-blue-600">← Dashboard</Link>
    <h1 className="mt-5 text-2xl font-semibold">Needs your review</h1>
    <p className="mt-2 text-sm text-slate-500">Check suggestions, edit where needed, then approve or dismiss.</p>
    {error && <p role="alert" className="mt-4 text-sm text-amber-700">Review counts could not be updated. <button onClick={() => void refresh()} className="underline">Retry</button></p>}
    <div className="mt-6 grid gap-4 sm:grid-cols-3">{([
      ["Sources", summary?.source_reviews, "/workspace/sources?status=NEEDS_REVIEW", "file", "Check AI classifications of your original information."],
      ["Knowledge", summary?.knowledge_reviews, "/workspace/knowledge?status=SUGGESTED", "note", "Review extracted facts and knowledge drafts."],
      ["Actions", summary?.action_reviews, "/workspace/actions?status=SUGGESTED", "check", "Review suggested tasks and your requested action drafts."],
    ] as const).map(([label, count, path, icon, description]) => <Link key={label} to={path} className="rounded-xl border border-slate-200 bg-white p-5 hover:border-blue-300"><div className="flex items-center justify-between"><h2 className="font-semibold">{label}</h2><WorkspaceIcon name={icon} className="h-5 w-5 text-blue-600" /></div><p className="my-4 text-3xl font-semibold">{count ?? "—"}</p><p className="text-sm leading-relaxed text-slate-500">{description}</p><p className="mt-5 text-sm text-blue-600">Review {label.toLowerCase()} →</p></Link>)}</div>
    <Link to="/email-drafts" className="mt-5 block rounded-xl border border-slate-200 bg-white p-5 text-sm text-blue-600">Email drafts awaiting review: {summary?.draft_reviews ?? 0} →</Link>
  </main>;
}
