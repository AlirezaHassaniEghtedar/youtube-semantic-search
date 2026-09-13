import { useState } from "react";
import { apiFetch } from "../lib/api";
import { useToast } from "../context/ToastContext";
import { ResultCard } from "../components/ResultCard";
import { Button } from "../components/Button";
import type { Channel, SearchResult } from "../types";

// Standalone semantic search — same ResultCard component that chat sources use.

export function SearchView({ channels }: { channels: Channel[] }) {
  const { showToast } = useToast();
  const [query, setQuery] = useState("");
  const [channelId, setChannelId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      showToast("Please enter a search query", "error");
      return;
    }

    setLoading(true);
    setResults(null);

    const payload: Record<string, unknown> = { query: trimmed, limit: 20 };
    if (channelId) payload.channel_id = channelId;
    if (dateFrom) payload.date_from = new Date(dateFrom).toISOString();
    if (dateTo) payload.date_to = new Date(dateTo + "T23:59:59").toISOString();

    try {
      const data = await apiFetch<SearchResult[]>("/api/search", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setResults(data);
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  function handleClear() {
    setQuery("");
    setChannelId("");
    setDateFrom("");
    setDateTo("");
    setResults(null);
  }

  return (
    <div className="view-stack">
      <section className="card">
        <h2 className="card__title">Semantic Search</h2>
        <form className="form" onSubmit={handleSubmit}>
          <div className="form__row">
            <label htmlFor="search-query" className="form__label">Search Query</label>
            <input
              type="text"
              id="search-query"
              className="form__input"
              placeholder="Search in Persian or English…"
              dir="auto"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="form__row form__row--inline">
            <div className="form__field">
              <label htmlFor="search-channel" className="form__label">Channel (optional)</label>
              <select
                id="search-channel"
                className="form__select"
                value={channelId}
                onChange={(e) => setChannelId(e.target.value)}
              >
                <option value="">All channels</option>
                {channels.map((ch) => (
                  <option key={ch.id} value={ch.id}>
                    {ch.name || ch.url}
                  </option>
                ))}
              </select>
            </div>
            <div className="form__field">
              <label htmlFor="search-date-from" className="form__label">From (optional)</label>
              <input
                type="date"
                id="search-date-from"
                className="form__input"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </div>
            <div className="form__field">
              <label htmlFor="search-date-to" className="form__label">To (optional)</label>
              <input
                type="date"
                id="search-date-to"
                className="form__input"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </div>
          </div>
          <div className="form__actions-row">
            <Button type="submit" loading={loading}>Search</Button>
            <Button variant="ghost" onClick={handleClear}>Clear</Button>
          </div>
        </form>
      </section>

      {results !== null && (
        <section className="card">
          <div className="search-results">
            {results.length === 0 ? (
              <p className="empty-state">No matching segments found.</p>
            ) : (
              results.map((r) => <ResultCard key={r.segment_id} result={r} />)
            )}
          </div>
        </section>
      )}
    </div>
  );
}
