interface PlaceholderPageProps {
  title: string;
  description?: string;
}

/**
 * Temporary placeholder used by the scaffold. Real page implementations
 * replace these in later tasks.
 */
export default function PlaceholderPage({
  title,
  description,
}: PlaceholderPageProps) {
  return (
    <section className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-2 text-sm text-slate-500">
        {description ?? "This page is scaffolded and will be implemented soon."}
      </p>
    </section>
  );
}
