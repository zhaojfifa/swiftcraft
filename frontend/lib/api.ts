export type InputMetadata = {
  duration?: number | null;
  width?: number | null;
  height?: number | null;
};

export type TaskRecord = {
  id: string;
  service: string;
  mode: string;
  stage: string;
  progress: number;
  logs: string[];
  result_url?: string | null;
  thumbnail_url?: string | null;
  input_metadata?: InputMetadata | null;
  is_mock: boolean;
  created_at: string;
  updated_at: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:10000";

export async function createTask({
  videoFile,
  imageFile,
  mode,
  service
}: {
  videoFile: File;
  imageFile: File;
  mode: string;
  service: string;
}): Promise<{ task_id: string }> {
  const form = new FormData();
  form.append("video_file", videoFile);
  form.append("image_file", imageFile);
  form.append("mode", mode);
  form.append("service", service);

  const response = await fetch(`${API_BASE}/api/v1/tasks`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    throw new Error("Failed to create task.");
  }

  return response.json();
}

export async function fetchTask(taskId: string): Promise<TaskRecord> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Failed to fetch task.");
  }
  return response.json();
}

export async function fetchTasks(limit = 20): Promise<TaskRecord[]> {
  const response = await fetch(`${API_BASE}/api/v1/tasks?limit=${limit}`, {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Failed to fetch tasks.");
  }
  return response.json();
}
