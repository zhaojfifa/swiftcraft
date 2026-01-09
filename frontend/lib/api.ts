export type CreateTaskPayload = {
  service: string;
  mode: string;
  input_key: string;
};

export type CreateTaskResponse = {
  task_id: string;
};

export type TaskStatus = 'queued' | 'running' | 'done' | 'failed';

export type TaskRecord = {
  task_id: string;
  id?: string;
  service?: string;
  mode?: string;
  status?: TaskStatus | string;
  progress?: number;
  output_key?: string | null;
  output_url?: string | null;
  error?: string | null;
  logs?: string[] | null;
  created_at?: string;
  updated_at?: string;
};

function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE || '';
  return base.replace(/\/+$/, '');
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  if (!text) throw new Error(`Empty response (HTTP ${res.status})`);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Non-JSON response (HTTP ${res.status}): ${text.slice(0, 300)}`);
  }
}

export async function createTask(payload: CreateTaskPayload): Promise<CreateTaskResponse> {
  const base = getApiBase();
  if (!base) throw new Error('NEXT_PUBLIC_API_BASE is not set');

  const res = await fetch(`${base}/api/v1/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });

  if (!res.ok) {
    const err = await readJson<{ detail?: any }>(res).catch(() => ({ detail: 'Unknown error' }));
    throw new Error(`createTask failed (HTTP ${res.status}): ${JSON.stringify(err.detail ?? err)}`);
  }

  const data = await readJson<CreateTaskResponse>(res);
  if (!data?.task_id) throw new Error('createTask: missing task_id');
  return data;
}

export async function getTask(taskId: string): Promise<TaskRecord> {
  const base = getApiBase();
  if (!base) throw new Error('NEXT_PUBLIC_API_BASE is not set');

  const res = await fetch(`${base}/api/v1/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
    cache: 'no-store',
  });

  if (!res.ok) {
    const err = await readJson<{ detail?: any }>(res).catch(() => ({ detail: 'Unknown error' }));
    throw new Error(`getTask failed (HTTP ${res.status}): ${JSON.stringify(err.detail ?? err)}`);
  }

  const data = await readJson<TaskRecord>(res);
  return data;
}
