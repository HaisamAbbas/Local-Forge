import { useCallback, useEffect, useState } from "react";

import { fetchApiHealth, type ApiHealth } from "../lib/api";

type ConnectionState =
  { status: "checking" } | { status: "connected"; health: ApiHealth } | { status: "unavailable" };

export function ApiStatus() {
  const [connection, setConnection] = useState<ConnectionState>({ status: "checking" });

  const checkConnection = useCallback(async (signal?: AbortSignal) => {
    try {
      const health = await fetchApiHealth(signal);
      setConnection({ status: "connected", health });
    } catch {
      if (!signal?.aborted) {
        setConnection({ status: "unavailable" });
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const initialCheck = window.setTimeout(() => void checkConnection(controller.signal), 0);
    const timer = window.setInterval(() => void checkConnection(controller.signal), 5000);

    return () => {
      controller.abort();
      window.clearTimeout(initialCheck);
      window.clearInterval(timer);
    };
  }, [checkConnection]);

  const statusText =
    connection.status === "checking"
      ? "Checking local API"
      : connection.status === "connected"
        ? "Local API connected"
        : "Local API unavailable";

  return (
    <div className={`api-status api-status--${connection.status}`}>
      <span className="status-indicator" aria-hidden="true" />
      <span role="status" aria-live="polite">
        {statusText}
        {connection.status === "connected" && (
          <small>
            API {connection.health.api_version} · schema {connection.health.database.schema_version}
          </small>
        )}
        {connection.status === "unavailable" && (
          <small>Start the local API, then retry the connection.</small>
        )}
      </span>
      {connection.status === "unavailable" && (
        <button
          className="retry-button"
          onClick={() => {
            setConnection({ status: "checking" });
            void checkConnection();
          }}
          type="button"
        >
          Retry connection
        </button>
      )}
    </div>
  );
}
