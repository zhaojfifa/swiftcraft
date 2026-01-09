export type CreateTaskPayload = {
  service: string;
  mode: string;

  // Preset/R2 mock mode
  input_key?: string;

  // Upload mode (WorkspaceClient uses these)
  videoFile?: File | null;
  imageFile?: File | null;

  // Allow forward-compatible extra fields
  [k: string]: any;
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

  const hasFiles = !!payload.videoFile || !!payload.imageFile;

  let res: Response;

  if (hasFiles) {
    const fd = new FormData();
    fd.append('service', payload.service);
    fd.append('mode', payload.mode);

    if (payload.videoFile) fd.append('video_file', payload.videoFile);
    if (payload.imageFile) fd.append('image_file', payload.imageFile);

    for (const [key, value] of Object.entries(payload)) {
      if (['service', 'mode', 'videoFile', 'imageFile'].includes(key)) continue;
      if (value === undefined || value === null) continue;
      fd.append(key, typeof value === 'string' ? value : JSON.stringify(value));
    }

    res = await fetch(`${base}/api/v1/tasks`, {
      method: 'POST',
      body: fd,
      cache: 'no-store',
    });
  } else {
    res = await fetch(`${base}/api/v1/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      cache: 'no-store',
    });
  }

  const text = await res.text();
  let json: any = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }

  if (!res.ok) {
    const detail = json?.detail ?? text ?? 'Unknown error';
    throw new Error(
      `createTask failed (HTTP ${res.status}): ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`
    );
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
