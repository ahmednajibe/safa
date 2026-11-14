import type {
  Health,
  OperationsResponse,
  ResolveResponse,
} from "./types";

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/api/health");
}

export function getOperations(): Promise<OperationsResponse> {
  return getJson<OperationsResponse>("/api/operations");
}

export async function resolveOps(
  ops: Record<string, Record<string, unknown>>,
): Promise<ResolveResponse> {
  const r = await fetch("/api/operations/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ ops }),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return (await r.json()) as ResolveResponse;
}
