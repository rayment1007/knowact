import { Link } from "react-router-dom";
import { EmptyState } from "@/components/feedback";

/**
 * Route shell reserved for the unified verification queue in Phase 7.
 * It intentionally contains no fabricated counts or review records.
 */
export default function VerificationPage() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Verification</h1>
        <p className="mt-1 text-sm text-slate-500">
          Review AI suggestions in one place before they become confirmed
          knowledge or actions.
        </p>
      </header>

      <EmptyState
        title="The unified review queue is not available yet."
        description="You can continue reviewing knowledge suggestions in the Knowledge Hub. Email task suggestions remain available under Gmail."
        action={
          <Link
            to="/knowledge"
            className="inline-flex rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700"
          >
            Open Knowledge Hub
          </Link>
        }
      />
    </section>
  );
}
