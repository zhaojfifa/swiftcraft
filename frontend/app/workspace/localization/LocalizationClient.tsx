"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { ApiHttpError, createTask, getTask, getUploadUrl, TaskRecord } from "../../../lib/api";
import { resolveAssetUrl } from "../../../lib/url";
import InputPanel from "./components/InputPanel";
import LogsPanel from "./components/LogsPanel";
import OutputTabs from "./components/OutputTabs";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "done"]);

function shouldStopPolling(task: TaskRecord | null): boolean {
  const status = String(task?.status || "").toLowerCase();
  const stage = String(task?.stage || "").toLowerCase();
  return TERMINAL_STATUSES.has(status) || stage === "done" || stage === "failed" || Boolean(task?.output_url);
}

export default function LocalizationClient() {
  const [mode, setMode] = useState<"baseline" | "intelligent" | "enhanced">("baseline");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [inputVideoUrl, setInputVideoUrl] = useState<string | null>(null);
  const [preserveBgm, setPreserveBgm] = useState(true);
  const [ducking, setDucking] = useState(true);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const pollTokenRef = useRef(0);
  const startedAtRef = useRef<number>(0);

  const taskId = task?.task_id || "";
  const logs = useMemo(() => task?.logs || [], [task?.logs]);
  const outputs = task?.metadata?.outputs || {};
  const outputUrl = resolveAssetUrl(task?.output_url || null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, []);

  useEffect(() => {
    if (!videoFile) {
      setInputVideoUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(videoFile);
    setInputVideoUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [videoFile]);

  const uploadFileToR2 = async (file: File): Promise<string> => {
    const upload = await getUploadUrl({
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      purpose: "uploads",
    });
    const putRes = await fetch(upload.upload_url, {
      method: "PUT",
      headers: upload.headers,
      body: file,
    });
    if (!putRes.ok) {
      throw new Error(`Upload failed (HTTP ${putRes.status})`);
    }
    return upload.file_key;
  };

  const fetchTaskFromCdn = async (id: string): Promise<TaskRecord> => {
    const res = await fetch(`https://cdn.swiftcraft.ai/tasks/${encodeURIComponent(id)}.json`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      throw new ApiHttpError(`CDN get task failed (${res.status})`, res.status);
    }
    const text = await res.text();
    return JSON.parse(text) as TaskRecord;
  };

  const startPolling = (id: string) => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollTokenRef.current += 1;
    const token = pollTokenRef.current;
    let attempt = 0;
    startedAtRef.current = Date.now();

    const scheduleNext = () => {
      const delay = Math.min(15000, 1000 * Math.pow(2, attempt));
      attempt += 1;
      pollRef.current = window.setTimeout(tick, delay);
    };

    const tick = async () => {
      if (pollTokenRef.current !== token) return;
      try {
        let latest: TaskRecord;
        try {
          latest = await fetchTaskFromCdn(id);
        } catch {
          latest = await getTask(id);
        }
        setTask(latest);
        const elapsed = Date.now() - startedAtRef.current;
        if (elapsed > 120000 && !shouldStopPolling(latest)) {
          setWarning("Still processing... you can keep this tab open or refresh later.");
        } else {
          setWarning(null);
        }
        if (String(latest.status || "").toLowerCase() === "failed") {
          setError(latest.error || "Task failed.");
        } else {
          setError(null);
        }
        if (shouldStopPolling(latest)) {
          setIsRunning(false);
          return;
        }
      } catch (err) {
        const status = err instanceof ApiHttpError ? err.status : undefined;
        if (status === 502 || status === 503) {
          setWarning(`Temporary gateway issue, retrying... (HTTP ${status})`);
          scheduleNext();
          return;
        }
        setWarning("Temporary polling issue, retrying...");
      }
      scheduleNext();
    };

    tick();
  };

  const handleRun = async () => {
    if (!videoFile) {
      setError("Please upload a source video.");
      return;
    }
    if (mode !== "baseline") {
      setError("Only baseline mode is enabled for localization.");
      return;
    }
    setError(null);
    setWarning(null);
    setTask(null);
    setIsRunning(true);
    try {
      const inputKey = await uploadFileToR2(videoFile);
      const res = await createTask({
        service_type: "localization",
        mode: "baseline",
        input_key: inputKey,
        inputs: {
          target_lang: "my",
          voice_id: "mm_female_1",
          subtitle_mode: "sidecar",
          preserve_bgm: preserveBgm,
          ducking,
          lipsync_enabled: false,
        },
      });
      startPolling(res.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Failed to create localization task.");
    }
  };

  const lowerError = String(error || "").toLowerCase();
  const hasPolicyViolation =
    lowerError.includes("content_policy_violation") ||
    logs.some((line) => line.toLowerCase().includes("content_policy_violation"));

  return (
    <div className="h-screen bg-slate-50 text-slate-900 flex overflow-hidden">
      <InputPanel
        mode={mode}
        setMode={setMode}
        videoFile={videoFile}
        setVideoFile={setVideoFile}
        inputVideoUrl={inputVideoUrl}
        preserveBgm={preserveBgm}
        setPreserveBgm={setPreserveBgm}
        ducking={ducking}
        setDucking={setDucking}
        isRunning={isRunning}
        onRun={handleRun}
      />
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-xl font-bold">Localization Workspace</h1>
            <p className="text-sm text-slate-500">Source video -&gt; Burmese subtitle + dubbed audio + localized video</p>
          </div>
          <Link href="/" className="text-sm text-blue-600 underline">
            Back
          </Link>
        </div>

        {taskId ? (
          <div className="mb-3 rounded border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
            Task ID: {taskId}
          </div>
        ) : null}
        {warning ? <div className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{warning}</div> : null}
        {error ? <div className="mb-3 rounded border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</div> : null}
        {hasPolicyViolation ? (
          <div className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            Safety checker blocked this content. Try safer reference material or prompt text.
          </div>
        ) : null}

        <OutputTabs outputUrl={outputUrl} outputs={outputs} />
        <LogsPanel status={String(task?.status || "")} stage={String(task?.stage || "")} logs={logs} />
      </div>
    </div>
  );
}

