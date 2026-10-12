export type ApiHealth = {
  status: "ok";
  api_version: "v1";
  app_version: string;
  database: {
    status: "ready";
    schema_version: number;
  };
};

function isApiHealth(value: unknown): value is ApiHealth {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Partial<ApiHealth>;
  return (
    candidate.status === "ok" &&
    candidate.api_version === "v1" &&
    typeof candidate.app_version === "string" &&
    typeof candidate.database === "object" &&
    candidate.database !== null &&
    candidate.database.status === "ready" &&
    typeof candidate.database.schema_version === "number"
  );
}

export async function fetchApiHealth(signal?: AbortSignal): Promise<ApiHealth> {
  const response = await fetch("/health", {
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    throw new Error("Local API returned an unsuccessful health response.");
  }

  const payload: unknown = await response.json();
  if (!isApiHealth(payload)) {
    throw new Error("Local API returned an invalid health response.");
  }

  return payload;
}
