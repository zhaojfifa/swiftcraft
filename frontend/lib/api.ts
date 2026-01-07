export type TaskRecord = {
  task_id?: string;
  id?: string;
  service?: "swap" | "avatar";
  mode?: "baseline" | "intelligent";
  status?: "queued" | "running" | "done" | "failed";
  stage?: string;
  progress?: number;
  created_at?: string;
  input_video_url?: string | null;
  input_image_url?: string | null;
  thumb_url?: string | null;
  output_url?: string | null;
  logs?: string[];
  error?: string | null;
  metadata?: Record<string, unknown>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:10000";

export async function createTask(params: {
  videoFile: File;
  imageFile: File;
  mode: string;
  service: string;
  faceEnhancer?: boolean;
}): Promise<{ task_id: string }> {
  const form = new FormData();
  form.append("video_file", params.videoFile);
  form.append("image_file", params.imageFile);
  form.append("mode", params.mode);
  form.append("service", params.service);
  if (params.faceEnhancer !== undefined) {
    form.append("face_enhancer", String(params.faceEnhancer));
  }

  const response = await fetch(`${API_BASE}/api/v1/tasks`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    throw new Error("Failed to create task.");
  }

  return response.json();
}

export async function getTask(taskId: string): Promise<TaskRecord> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Failed to fetch task.");
  }
  return response.json();
}
