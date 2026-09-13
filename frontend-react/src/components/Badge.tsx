// Badge — matches the vanilla .badge / .badge--<status> classes.
// Status values come straight from the API (pending, fetching_list, done, ...),
// so underscores are replaced with spaces for display and used as the CSS hook.

export function Badge({
  status,
  kind,
}: {
  status: string;
  kind?: "status" | "video-type";
}) {
  if (kind === "video-type") {
    return (
      <span className="badge badge--video-type">
        {status.replace(/_/g, " ")}
      </span>
    );
  }
  return (
    <span className={`badge badge--${status}`}>{status.replace(/_/g, " ")}</span>
  );
}
