export type CreateTaskPayload = {
  service: string;
  mode: string;
  input_key?: string;
  face_enhancer?: string | null;
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
  stage?: string;
  progress?: number;
  thumb_url?: string | null;
  output_key?: string | null;
  output_url?: string | null;
  error?: string | null;
  logs?: string[] | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export type UploadUrlRequest = {
  filename: string;
  content_type: string;
  purpose?: string;
};

export type UploadUrlResponse = {
  file_key: string;
  upload_url: string;
  public_url: string;
  expires_in: number;
  headers: Record<string, string>;
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

  const service = (payload.service || '').toLowerCase();
  const mode = (payload.mode || '').toLowerCase();

  const body: Record<string, unknown> = { service, mode };
  if (payload.input_key) body.input_key = payload.input_key;
  if (payload.face_enhancer !== undefined) body.face_enhancer = payload.face_enhancer;

  const res = await fetch(`${base}/api/v1/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });

  const text = await res.text();
  if (!res.ok) {
    throw new Error(`POST /api/v1/tasks failed (${res.status}): ${text}`);
  }

  let json: any = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }

  if (!json?.task_id) throw new Error('createTask: missing task_id');
  return json as CreateTaskResponse;
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

export async function getUploadUrl(payload: UploadUrlRequest): Promise<UploadUrlResponse> {
  const base = getApiBase();
  if (!base) throw new Error('NEXT_PUBLIC_API_BASE is not set');

  const res = await fetch(`${base}/api/v1/upload-url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });

  if (!res.ok) {
    const err = await readJson<{ detail?: any }>(res).catch(() => ({ detail: 'Unknown error' }));
    throw new Error(`getUploadUrl failed (HTTP ${res.status}): ${JSON.stringify(err.detail ?? err)}`);
  }

  return readJson<UploadUrlResponse>(res);
}
