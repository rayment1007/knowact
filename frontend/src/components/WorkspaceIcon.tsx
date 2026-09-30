export type IconName = "search" | "settings" | "sync" | "plus" | "assistant" | "close" | "file" | "note" | "calendar" | "email" | "arrow" | "check" | "home" | "workspace";
const paths: Record<IconName, string> = {
  home: "m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9",
  workspace: "M3 7h18v14H3V7Zm5 0V3h8v4M3 12h18M10 12v3h4v-3",
  check: "M4 4h16v16H4V4Zm4 8 3 3 5-6",
  search: "m21 21-4.5-4.5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  settings: "m9 3-1 3-3 1-2 3 2 2-1 3 3 2 3-1 2 2 3-1 1-3 3-1 2-3-2-2 1-3-3-2-3 1-2-2ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  sync: "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-2L20 8M4 16l2.4 3A7 7 0 0 0 18 17",
  plus: "M12 5v14M5 12h14",
  assistant: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z",
  close: "m6 6 12 12M18 6 6 18",
  file: "M14 3H5v18h14V8l-5-5ZM14 3v6h5M8 13h8M8 17h5",
  note: "M5 3h14v18H5V3ZM8 7h8M8 11h8M8 15h5",
  calendar: "M4 5h16v16H4V5ZM4 10h16M8 3v4M16 3v4M8 14h2M14 14h2M8 17h2",
  email: "M3 5h18v14H3V5Zm0 1 9 7 9-7",
  arrow: "M5 12h14m-5-5 5 5-5 5",
};
export default function WorkspaceIcon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}><path d={paths[name]} /></svg>;
}
