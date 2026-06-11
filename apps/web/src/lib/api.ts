const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_API_KEY = process.env.NEXT_PUBLIC_SENTINEX_API_KEY || "";

export interface Workspace {
  id: string;
  name: string;
  created_at: string;
}

export interface ScanSummary {
  id: string;
  status: string;
  risk_score: number | null;
  created_at: string;
}

export interface ScanDetail {
  id: string;
  status: string;
  risk_score: number | null;
  started_at: string | null;
  finished_at: string | null;
  agent_id: string;
  scenario_ids: string[];
}

export interface ScanEvent {
  scan_id: string;
  seq: number;
  ts: string | null;
  type: string;
  payload: Record<string, unknown>;
  _replay?: boolean;
}

export interface Finding {
  id: string;
  category: string;
  rule_id: string;
  severity: string;
  title: string;
  evidence: Record<string, unknown> | null;
  cwe: string[] | null;
  created_at: string;
}

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  apiKey: string = DEFAULT_API_KEY
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { "X-Api-Key": apiKey } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

export async function listScans(
  workspaceId: string,
  apiKey?: string
): Promise<ScanSummary[]> {
  return apiFetch(`/workspace/${workspaceId}/scan`, undefined, apiKey);
}

export async function getScan(
  workspaceId: string,
  scanId: string,
  apiKey?: string
): Promise<ScanDetail> {
  return apiFetch(`/workspace/${workspaceId}/scan/${scanId}`, undefined, apiKey);
}

export async function listScanEvents(
  workspaceId: string,
  scanId: string,
  fromSeq = 0,
  limit = 500,
  apiKey?: string
): Promise<{ events: ScanEvent[]; count: number; has_more: boolean }> {
  return apiFetch(
    `/workspace/${workspaceId}/scan/${scanId}/events?from_seq=${fromSeq}&limit=${limit}`,
    undefined,
    apiKey
  );
}

export async function listFindings(
  workspaceId: string,
  scanId: string,
  severity?: string,
  apiKey?: string
): Promise<Finding[]> {
  const qs = severity
    ? `?${new URLSearchParams({ severity }).toString()}`
    : "";
  return apiFetch(
    `/workspace/${workspaceId}/scan/${scanId}/findings${qs}`,
    undefined,
    apiKey
  );
}

export { API_BASE };
