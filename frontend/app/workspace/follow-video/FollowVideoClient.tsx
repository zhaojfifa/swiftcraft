"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiHttpError, createTask, getTask, getUploadUrl, TaskRecord } from "../../../lib/api";
import { resolveAssetUrl } from "../../../lib/url";

type Mode = "basic" | "intelligence";
type DurationSec = 5 | 8 | 10;
type AspectRatio = "9:16" | "16:9" | "1:1";
type FollowStrength = "low" | "medium" | "high";
type ReferenceMix = "a_dominant" | "balanced" | "b_dominant";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "done"]);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function shouldStopPolling(task: TaskRecord | null): boolean {
  const status = String(task?.status || "").toLowerCase();
  const stage = String(task?.stage || "").toLowerCase();
  return TERMINAL_STATUSES.has(status) || stage === "done" || stage === "failed";
}

export default function FollowVideoClient() {
  const [mode, setMode] = useState<Mode>("basic");
  const [subjectImage, setSubjectImage] = useState<File | null>(null);
  const [referenceVideoA, setReferenceVideoA] = useState<File | null>(null);
  const [referenceVideoB, setReferenceVideoB] = useState<File | null>(null);
  const [prompt, setPrompt] = useState("");
  const [durationSec, setDurationSec] = useState<DurationSec>(5);
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>("9:16");
  const [followStrength, setFollowStrength] = useState<FollowStrength>("medium");
  const [referenceMix, setReferenceMix] = useState<ReferenceMix>("balanced");

  const [subjectPreview, setSubjectPreview] = useState<string | null>(null);
  const [referencePreviewA, setReferencePreviewA] = useState<string | null>(null);
  const [referencePreviewB, setReferencePreviewB] = useState<string | null>(null);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const pollTokenRef = useRef(0);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, []);

  useEffect(() => {
    if (!subjectImage) {
      setSubjectPreview(null);
      return;
    }
    const objectUrl = URL.createObjectURL(subjectImage);
    setSubjectPreview(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [subjectImage]);

  useEffect(() => {
    if (!referenceVideoA) {
      setReferencePreviewA(null);
      return;
    }
    const objectUrl = URL.createObjectURL(referenceVideoA);
    setReferencePreviewA(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [referenceVideoA]);

  useEffect(() => {
    if (!referenceVideoB) {
      setReferencePreviewB(null);
      return;
    }
    const objectUrl = URL.createObjectURL(referenceVideoB);
    setReferencePreviewB(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [referenceVideoB]);

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

  const startPolling = (taskId: string) => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollTokenRef.current += 1;
    const token = pollTokenRef.current;
    let attempt = 0;

    const tick = async () => {
      if (pollTokenRef.current !== token) return;
      try {
        const latest = await getTask(taskId);
        setTask(latest);
        if (String(latest.status || "").toLowerCase() === "failed") {
          setError(latest.error || "Task failed.");
          setIsRunning(false);
          return;
        }
        if (shouldStopPolling(latest)) {
          setIsRunning(false);
          return;
        }
      } catch (err) {
        const status = err instanceof ApiHttpError ? err.status : undefined;
        if (status === 502 || status === 503) {
          setWarning(`Temporary gateway issue, retrying... (HTTP ${status})`);
        } else {
          setWarning("Temporary polling issue, retrying...");
        }
      }
      const delay = Math.min(15000, 1000 * Math.pow(2, attempt));
      attempt += 1;
      pollRef.current = window.setTimeout(tick, delay);
    };

    tick();
  };

  const handleRun = async () => {
    if (!subjectImage || !referenceVideoA || !referenceVideoB) {
      setError("Please upload a subject image and both reference videos.");
      return;
    }
    setError(null);
    setWarning(mode === "intelligence" ? "Intelligence is placeholder-only for now." : null);
    setTask(null);
    setIsRunning(true);
    try {
      const [subjectImageKey, referenceVideoAKey, referenceVideoBKey] = await Promise.all([
        uploadFileToR2(subjectImage),
        uploadFileToR2(referenceVideoA),
        uploadFileToR2(referenceVideoB),
      ]);
      const result = await createTask({
        service_type: "follow_video",
        mode,
        input_key: referenceVideoAKey,
        inputs: {
          subject_image: subjectImageKey,
          reference_video_a: referenceVideoAKey,
          reference_video_b: referenceVideoBKey,
          prompt,
          duration_sec: durationSec,
          aspect_ratio: aspectRatio,
          follow_strength: followStrength,
          reference_mix: referenceMix,
        },
      });
      startPolling(result.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Failed to create Follow Video task.");
    }
  };

  const taskMetadata = asRecord(task?.metadata);
  const taskOutputs = asRecord(taskMetadata.outputs);
  const manifestPreview = asRecord(taskMetadata.manifest_preview);
  const manifestOutputs = asRecord(manifestPreview.outputs);
  const outputVideoUrl = resolveAssetUrl(
    pickString(task?.output_url, taskOutputs.video_url, manifestOutputs.video_url),
  );
  const manifestUrl = resolveAssetUrl(pickString(taskOutputs.manifest_url, manifestOutputs.manifest_url));
  const logs = useMemo(() => task?.logs || [], [task?.logs]);

  const requestPreview = {
    service_type: "follow_video",
    mode,
    input_key: referenceVideoA ? "(uploaded key)" : "",
    inputs: {
      subject_image: subjectImage ? "(uploaded key)" : "",
      reference_video_a: referenceVideoA ? "(uploaded key)" : "",
      reference_video_b: referenceVideoB ? "(uploaded key)" : "",
      prompt,
      duration_sec: durationSec,
      aspect_ratio: aspectRatio,
      follow_strength: followStrength,
      reference_mix: referenceMix,
    },
  };

  return (
    <div className="h-screen bg-white text-slate-900 flex flex-col font-sans overflow-hidden">
      <nav className="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0 z-20 shadow-sm">
        <div className="flex items-center gap-4">
          <Link href="/" className="font-bold text-slate-900 tracking-tight hover:text-cyan-600 transition">
            SwiftCraft
          </Link>
          <span className="text-slate-300">/</span>
          <span className="font-medium text-slate-600">Follow Video</span>
        </div>

        <div className="bg-slate-100 p-1 rounded-lg border border-slate-200 flex relative">
          <button
            onClick={() => setMode("basic")}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === "basic"
                ? "bg-white text-slate-900 shadow-sm border border-slate-200 ring-1 ring-black/5"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Basic
          </button>
          <button
            onClick={() => setMode("intelligence")}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === "intelligence"
                ? "bg-white text-cyan-600 shadow-sm border border-slate-200 ring-1 ring-black/5"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Intelligence
          </button>
        </div>

        <div className="text-xs font-medium text-slate-500 bg-slate-50 px-2 py-1 rounded border border-slate-200">
          PREVIEW
        </div>
      </nav>

      <div className="flex-1 grid grid-cols-[360px_minmax(0,1fr)] overflow-hidden">
        <div className="border-r border-slate-200 bg-white p-6 overflow-y-auto space-y-6">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-400 mb-2">Follow Video / {mode === "basic" ? "Basic" : "Intelligence"}</div>
            <h1 className="text-2xl font-semibold text-slate-900">Create Follow Video</h1>
            <p className="mt-2 text-sm text-slate-500">
              Build a placeholder Follow Video task with one subject image, two reference videos, and a prompt.
            </p>
            {mode === "intelligence" ? (
              <div className="mt-3 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-xs text-cyan-800">
                Intelligence is wired as a placeholder mode for now. The page skeleton and contract are stable.
              </div>
            ) : null}
          </div>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-slate-700">Subject Image</span>
            <input type="file" accept="image/*" onChange={(e) => setSubjectImage(e.target.files?.[0] || null)} className="block w-full text-sm text-slate-600" />
            {subjectPreview ? <img src={subjectPreview} alt="Subject preview" className="h-28 w-28 rounded-xl object-cover border border-slate-200" /> : null}
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-slate-700">Reference Video A</span>
            <input type="file" accept="video/*" onChange={(e) => setReferenceVideoA(e.target.files?.[0] || null)} className="block w-full text-sm text-slate-600" />
            {referencePreviewA ? <video src={referencePreviewA} className="w-full rounded-xl border border-slate-200" muted controls /> : null}
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-slate-700">Reference Video B</span>
            <input type="file" accept="video/*" onChange={(e) => setReferenceVideoB(e.target.files?.[0] || null)} className="block w-full text-sm text-slate-600" />
            {referencePreviewB ? <video src={referencePreviewB} className="w-full rounded-xl border border-slate-200" muted controls /> : null}
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-slate-700">Task Prompt</span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="Describe the follow-video behavior you want to preserve."
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-cyan-400"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Duration</span>
              <select value={durationSec} onChange={(e) => setDurationSec(Number(e.target.value) as DurationSec)} className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <option value={5}>5s</option>
                <option value={8}>8s</option>
                <option value={10}>10s</option>
              </select>
            </label>
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Aspect Ratio</span>
              <select value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value as AspectRatio)} className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <option value="9:16">9:16</option>
                <option value="16:9">16:9</option>
                <option value="1:1">1:1</option>
              </select>
            </label>
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Follow Strength</span>
              <select value={followStrength} onChange={(e) => setFollowStrength(e.target.value as FollowStrength)} className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Reference Mix</span>
              <select value={referenceMix} onChange={(e) => setReferenceMix(e.target.value as ReferenceMix)} className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <option value="a_dominant">A Dominant</option>
                <option value="balanced">Balanced</option>
                <option value="b_dominant">B Dominant</option>
              </select>
            </label>
          </div>

          <button
            onClick={handleRun}
            disabled={isRunning}
            className="w-full rounded-xl bg-cyan-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-cyan-500 disabled:cursor-not-allowed disabled:bg-cyan-300"
          >
            {isRunning ? "Submitting..." : "Run Follow Video"}
          </button>

          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 mb-2">Request Preview</div>
            <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-slate-700">{JSON.stringify(requestPreview, null, 2)}</pre>
          </div>
        </div>

        <div className="bg-slate-50/80 p-8 overflow-y-auto">
          <div className="mx-auto max-w-5xl space-y-6">
            {task?.task_id ? (
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                Task ID: {task.task_id}
              </div>
            ) : null}
            {warning ? <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{warning}</div> : null}
            {error ? <div className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</div> : null}

            <section className="rounded-3xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <div className="border-b border-slate-100 px-6 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Output Preview</div>
              </div>
              <div className="aspect-video bg-slate-950 text-slate-300 flex items-center justify-center">
                {outputVideoUrl ? (
                  <video controls className="h-full w-full object-contain" src={outputVideoUrl} />
                ) : (
                  <div className="px-6 text-center text-sm">
                    Placeholder output will appear here after task creation.
                  </div>
                )}
              </div>
              <div className="grid gap-2 border-t border-slate-100 bg-slate-50 px-6 py-4 text-sm text-slate-600 md:grid-cols-2">
                <div>Status: {task?.status || "idle"}</div>
                <div>Stage: {task?.stage || "waiting"}</div>
                <div>Mode: {task?.mode || mode}</div>
                <div>Route Summary: {String(taskMetadata.route_summary || asRecord(manifestPreview).route_summary || "follow_video_placeholder")}</div>
                <div>Provider: {String(taskMetadata.provider || "follow_video_placeholder")}</div>
                <div>Manifest: {manifestUrl ? <a href={manifestUrl} className="text-cyan-600 underline underline-offset-4" target="_blank" rel="noreferrer">Open</a> : "Pending"}</div>
              </div>
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <div className="border-b border-slate-100 px-6 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">SwiftFlow Engine Logs</div>
              </div>
              <div className="max-h-[28rem] overflow-y-auto bg-slate-950 px-6 py-5 font-mono text-xs text-cyan-100">
                {logs.length ? (
                  logs.map((line, index) => (
                    <div key={`${index}-${line.slice(0, 16)}`} className="whitespace-pre-wrap break-words py-0.5">
                      {line}
                    </div>
                  ))
                ) : (
                  <div className="text-slate-400">No logs yet. Submit a Follow Video placeholder task to see the runtime trace.</div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
