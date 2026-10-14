import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteReport, listReports, reportDownloadUrl, type SavedReport } from "../lib/api";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; reports: SavedReport[] };

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatReportSize(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Unknown";
  const units = ["B", "KB", "MB", "GB"];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

function formatCreated(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
}

function formatLabel(format: string): string {
  return format === "markdown" ? "Markdown" : format.toUpperCase();
}

/** Saved report files: download, delete with an inline confirmation, and filter by format. */
export function ReportsScreen() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [formatFilter, setFormatFilter] = useState("all");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      setState({ status: "ready", reports: await listReports() });
    } catch (error) {
      setState({
        status: "error",
        message: errorText(error, "Saved reports could not be loaded."),
      });
    }
  }, []);

  useEffect(() => {
    let active = true;
    listReports().then(
      (reports) => {
        if (active) setState({ status: "ready", reports });
      },
      (error: unknown) => {
        if (active)
          setState({
            status: "error",
            message: errorText(error, "Saved reports could not be loaded."),
          });
      },
    );
    return () => {
      active = false;
    };
  }, []);

  const reports = useMemo(() => (state.status === "ready" ? state.reports : []), [state]);
  const formats = useMemo(
    () => [...new Set(reports.map((report) => report.format))].sort(),
    [reports],
  );
  const visible =
    formatFilter === "all" ? reports : reports.filter((report) => report.format === formatFilter);

  const remove = async (report: SavedReport) => {
    setDeletingId(report.id);
    setActionError("");
    setNotice("");
    try {
      await deleteReport(report.id);
      setConfirmingId(null);
      setState((current) =>
        current.status === "ready"
          ? { status: "ready", reports: current.reports.filter((item) => item.id !== report.id) }
          : current,
      );
      setNotice(`Deleted “${report.title}”.`);
    } catch (error) {
      setActionError(errorText(error, "The report could not be deleted."));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="screen-stack">
      <section className="panel reports-panel" aria-labelledby="reports-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">SAVED EXPORTS</p>
            <h2 id="reports-title">Saved reports</h2>
            <p className="helper-copy">
              Reports are created from run comparisons on the Experiments screen. Files stay in the
              local reports folder; credentials are always omitted.
            </p>
          </div>
          <div className="reports-toolbar">
            {reports.length > 0 && (
              <label className="reports-filter">
                Format
                <select
                  onChange={(event) => setFormatFilter(event.target.value)}
                  value={formatFilter}
                >
                  <option value="all">All formats</option>
                  {formats.map((format) => (
                    <option key={format} value={format}>
                      {formatLabel(format)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button className="button button--quiet" onClick={() => void load()} type="button">
              Refresh
            </button>
          </div>
        </div>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
        {notice && (
          <p className="reports-notice" role="status">
            {notice}
          </p>
        )}
        {state.status === "loading" ? (
          <p role="status" aria-busy="true">
            Loading saved reports…
          </p>
        ) : state.status === "error" ? (
          <div className="state-message state-message--error" role="alert">
            <span>{state.message}</span>
            <button className="button button--quiet" onClick={() => void load()} type="button">
              Retry
            </button>
          </div>
        ) : reports.length === 0 ? (
          <div className="empty-state reports-empty">
            <div>
              <h3>No saved reports yet</h3>
              <p>
                Compare runs on the Experiments screen, then use Create report to save a Markdown,
                JSON or CSV export. Saved reports will be listed here.
              </p>
            </div>
            <a className="button button--primary" href="#experiments">
              Open Experiments
            </a>
          </div>
        ) : visible.length === 0 ? (
          <p className="empty-inline">
            No {formatLabel(formatFilter)} reports.{" "}
            <button
              className="button button--quiet"
              onClick={() => setFormatFilter("all")}
              type="button"
            >
              Show all formats
            </button>
          </p>
        ) : (
          <div className="table-scroll">
            <table className="reports-table">
              <caption className="visually-hidden">
                Saved reports, newest first ({visible.length})
              </caption>
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col">Format</th>
                  <th scope="col">Created</th>
                  <th scope="col">Size</th>
                  <th scope="col">Runs</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((report) => (
                  <tr key={report.id}>
                    <th scope="row">{report.title}</th>
                    <td>{formatLabel(report.format)}</td>
                    <td>{formatCreated(report.created_at)}</td>
                    <td>{formatReportSize(report.size_bytes)}</td>
                    <td>
                      {report.run_ids.length === 0 ? (
                        "None recorded"
                      ) : (
                        <ul className="reports-run-links">
                          {report.run_ids.map((runId) => (
                            <li key={runId}>
                              <a
                                aria-label={`Open run ${runId}`}
                                href={`#runs?run=${encodeURIComponent(runId)}`}
                              >
                                <code>{runId.slice(0, 8)}</code>
                              </a>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td>
                      {confirmingId === report.id ? (
                        <div
                          className="reports-confirm"
                          role="group"
                          aria-label={`Confirm deleting ${report.title}`}
                        >
                          <span>Delete this report file?</span>
                          <button
                            className="button button--danger-quiet"
                            disabled={deletingId === report.id}
                            onClick={() => void remove(report)}
                            type="button"
                          >
                            {deletingId === report.id ? "Deleting…" : "Confirm delete"}
                          </button>
                          <button
                            className="button button--quiet"
                            disabled={deletingId === report.id}
                            onClick={() => setConfirmingId(null)}
                            type="button"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="reports-actions">
                          {report.available ? (
                            <a
                              aria-label={`Download ${report.title}`}
                              className="button button--quiet"
                              download
                              href={reportDownloadUrl(report.id)}
                            >
                              Download
                            </a>
                          ) : (
                            <span className="inline-warning">File missing</span>
                          )}
                          <button
                            aria-label={`Delete ${report.title}`}
                            className="button button--danger-quiet"
                            onClick={() => {
                              setActionError("");
                              setNotice("");
                              setConfirmingId(report.id);
                            }}
                            type="button"
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
