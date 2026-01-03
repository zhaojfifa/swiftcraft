"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  TaskRecord,
  createTask,
  fetchTask,
  fetchTasks,
  resolveAssetUrl
} from "../lib/api";

type ServiceOption = {
  id: string;
  title: string;
  description: string;
  accent: string;
};

const SERVICES: ServiceOption[] = [
  {
    id: "swap",
    title: "Swap",
    description: "Replace subject with target identity while preserving motion.",
    accent: "from-emerald-400/40 to-emerald-200/10"
  },
  {
    id: "avatar",
    title: "Avatar",
    description: "Generate a stylized avatar track with adaptive lighting.",
    accent: "from-rose-400/40 to-rose-200/10"
  }
];

const MODES = [
  { id: "baseline", label: "Baseline" },
  { id: "intelligent", label: "Intelligent" }
];

export default function ExplorePage() {
  const [service, setService] = useState("swap");
  const [mode, setMode] = useState("baseline");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<TaskRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedService = useMemo(
    () => SERVICES.find((item) => item.id === service),
    [service]
  );

  const refreshTasks = useCallback(async () => {
    try {
      const data = await fetchTasks();
      setTasks(data);
      if (!activeTaskId && data.length > 0) {
        setActiveTaskId(data[0].id);
      }
    } catch (err) {
      setError("Unable to load task history.");
    }
  }, [activeTaskId]);

  const refreshTask = useCallback(
    async (taskId: string) => {
      const task = await fetchTask(taskId);
      setActiveTask(task);
      setTasks((prev) => {
        const other = prev.filter((item) => item.id !== task.id);
        return [task, ...other].slice(0, 20);
      });
      return task;
    },
    [setTasks]
  );

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  useEffect(() => {
    if (!activeTaskId) {
      setActiveTask(null);
      return;
    }
    let timer: NodeJS.Timeout;
    const poll = async () => {
      try {
        await refreshTask(activeTaskId);
      } catch (err) {
        setError("Polling failed. Is the backend running?");
      }
    };
    poll();
    timer = setInterval(poll, 2500);
    return () => clearInterval(timer);
  }, [activeTaskId, refreshTask]);

  const handleRun = async () => {
    if (!videoFile || !imageFile) {
      setError("Please upload both a source video and a target image.");
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await createTask({
        videoFile,
        imageFile,
        mode,
        service
      });
      setActiveTaskId(response.task_id);
      await refreshTask(response.task_id);
      await refreshTasks();
    } catch (err) {
      setError("Failed to start task.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const progressValue = Math.round((activeTask?.progress || 0) * 100);

  return (
    <main className="px-6 py-10 md:px-12">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 lg:flex-row">
        <section className="flex-1">
          <div className="mb-6">
            <p className="text-sm uppercase tracking-[0.3em] text-emerald-200/70">
              SwiftCraft Demo 1.1
            </p>
            <h1 className="mt-3 text-4xl font-semibold tracking-tight md:text-5xl">
              Explore the Mock Engine
            </h1>
            <p className="mt-3 max-w-2xl text-base text-slate-300">
              Upload a source video and a target image to simulate the SwiftCraft
              pipeline with staged progress and preset playback.
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {SERVICES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setService(item.id)}
                className={`group rounded-2xl border border-white/10 bg-gradient-to-br ${item.accent} p-4 text-left transition hover:border-white/30 ${
                  service === item.id ? "glow" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold">{item.title}</h3>
                  <span className="text-xs uppercase tracking-[0.2em] text-white/60">
                    Service
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-200/80">
                  {item.description}
                </p>
              </button>
            ))}
          </div>

          <div className="mt-6 flex gap-3">
            {MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setMode(item.id)}
                className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                  mode === item.id
                    ? "bg-emerald-400 text-black"
                    : "border border-white/10 text-white/70 hover:border-white/30"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="mt-8 grid gap-4 rounded-2xl border border-white/10 bg-white/5 p-6">
            <div>
              <label className="text-sm text-white/70">
                Source video (MP4)
              </label>
              <input
                type="file"
                accept="video/*"
                onChange={(event) =>
                  setVideoFile(event.target.files?.[0] || null)
                }
                className="mt-2 block w-full rounded-lg border border-white/10 bg-black/40 p-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="text-sm text-white/70">Target image</label>
              <input
                type="file"
                accept="image/*"
                onChange={(event) =>
                  setImageFile(event.target.files?.[0] || null)
                }
                className="mt-2 block w-full rounded-lg border border-white/10 bg-black/40 p-2 text-sm text-white"
              />
            </div>
            <button
              type="button"
              onClick={handleRun}
              disabled={isSubmitting}
              className="rounded-xl bg-emerald-400 px-5 py-3 text-sm font-semibold text-black transition hover:bg-emerald-300 disabled:opacity-50"
            >
              {isSubmitting ? "Starting..." : "Run Mock Task"}
            </button>
            {error ? (
              <p className="text-sm text-rose-300">{error}</p>
            ) : (
              <p className="text-xs text-white/50">
                Active service: {selectedService?.title} · Mode:{" "}
                {mode.toUpperCase()}
              </p>
            )}
          </div>

          <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">Live Status</h2>
                <p className="text-sm text-white/60">
                  {activeTask
                    ? `Stage: ${activeTask.stage}`
                    : "No task selected"}
                </p>
              </div>
              {activeTask?.is_mock ? (
                <span className="rounded-full border border-emerald-300/40 px-3 py-1 text-xs text-emerald-200/80">
                  Sandbox
                </span>
              ) : null}
            </div>

            <div className="mt-4 h-2 w-full rounded-full bg-white/10">
              <div
                className="h-2 rounded-full bg-emerald-400 transition-all"
                style={{ width: `${progressValue}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-white/60">{progressValue}%</p>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-white/10 bg-black/40 p-4">
                <h3 className="text-sm font-semibold">Input Snapshot</h3>
                {activeTask?.thumbnail_url ? (
                  <img
                    src={resolveAssetUrl(activeTask.thumbnail_url)}
                    alt="thumbnail"
                    className="mt-3 h-40 w-full rounded-lg object-cover"
                  />
                ) : (
                  <div className="mt-3 flex h-40 items-center justify-center rounded-lg border border-dashed border-white/20 text-xs text-white/40">
                    Thumbnail pending
                  </div>
                )}
                <div className="mt-3 text-xs text-white/60">
                  <p>
                    Duration:{" "}
                    {activeTask?.input_metadata?.duration?.toFixed(2) ?? "--"}s
                  </p>
                  <p>
                    Resolution:{" "}
                    {activeTask?.input_metadata?.width ?? "--"}x
                    {activeTask?.input_metadata?.height ?? "--"}
                  </p>
                </div>
              </div>

              <div className="rounded-xl border border-white/10 bg-black/40 p-4">
                <h3 className="text-sm font-semibold">Output Preview</h3>
                {activeTask?.result_url ? (
                  <div className="mt-3">
                    <video
                      controls
                      className="h-40 w-full rounded-lg bg-black"
                      src={resolveAssetUrl(activeTask.result_url)}
                    />
                    <a
                      href={resolveAssetUrl(activeTask.result_url)}
                      className="mt-3 inline-flex text-xs text-emerald-200 hover:text-emerald-100"
                    >
                      Download preset
                    </a>
                  </div>
                ) : (
                  <div className="mt-3 flex h-40 items-center justify-center rounded-lg border border-dashed border-white/20 text-xs text-white/40">
                    Output pending
                  </div>
                )}
              </div>
            </div>

            <div className="mt-6">
              <h3 className="text-sm font-semibold">Logs</h3>
              <div className="mt-2 max-h-32 space-y-2 overflow-y-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-white/60">
                {activeTask?.logs?.length ? (
                  activeTask.logs.map((log, index) => (
                    <p key={`${log}-${index}`}>{log}</p>
                  ))
                ) : (
                  <p>No logs yet.</p>
                )}
              </div>
            </div>
          </div>
        </section>

        <aside className="w-full max-w-md rounded-3xl border border-white/10 bg-black/40 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Task Vault</h2>
            <button
              type="button"
              onClick={refreshTasks}
              className="text-xs text-emerald-200/80 hover:text-emerald-200"
            >
              Refresh
            </button>
          </div>
          <div className="mt-4 space-y-3">
            {tasks.length === 0 ? (
              <div className="rounded-xl border border-dashed border-white/20 p-6 text-sm text-white/50">
                No tasks yet. Start a run to populate history.
              </div>
            ) : (
              tasks.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => setActiveTaskId(task.id)}
                  className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                    task.id === activeTaskId
                      ? "border-emerald-300/60 bg-emerald-300/10"
                      : "border-white/10 hover:border-white/30"
                  }`}
                >
                  <div className="flex items-center justify-between text-sm font-semibold">
                    <span>{task.service.toUpperCase()}</span>
                    <span className="text-xs text-white/50">
                      {task.mode.toUpperCase()}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-white/60">{task.stage}</p>
                  <div className="mt-2 h-1 rounded-full bg-white/10">
                    <div
                      className="h-1 rounded-full bg-emerald-300"
                      style={{ width: `${Math.round(task.progress * 100)}%` }}
                    />
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
