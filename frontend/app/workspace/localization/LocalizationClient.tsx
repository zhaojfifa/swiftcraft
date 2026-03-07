"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiHttpError, createTask, getTask, getUploadUrl, TaskRecord } from "../../../lib/api";
import { resolveAssetUrl } from "../../../lib/url";
import InputPanel from "./components/InputPanel";
import LogsPanel from "./components/LogsPanel";
import OutputTabs from "./components/OutputTabs";
import StagePanel from "./components/StagePanel";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "done"]);

type TopTab = "playground" | "json" | "api";
type OutputTab = "video" | "subtitles" | "audio" | "manifest";
type Mode = "baseline" | "intelligent";
type AudioStrategy = "mute_original" | "keep_bgm" | "duck_original";

type OutputMap = {
  video_url?: string;
  subtitle_url?: string;
  subtitle_ass_url?: string;
  audio_url?: string;
  manifest_url?: string;
  localized_audio_only_url?: string;
  localized_final_url?: string;
  manifest_json?: unknown;
  [k: string]: unknown;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function extractLocalizationOutputs(task: TaskRecord | null) {
  const metadata = asRecord(task?.metadata);
  const mdOutputs = asRecord(metadata.outputs) as OutputMap;
  const manifestPreview = asRecord(metadata.manifest_preview);
  const mpOutputs = asRecord(manifestPreview.outputs) as OutputMap;

  const merged: OutputMap = {
    ...mpOutputs,
    ...mdOutputs,
  };

  const videoUrl = pickString(
    merged.video_url,
    mdOutputs.video_url,
    mpOutputs.video_url,
    task?.output_url,
  );
  const subtitleUrl = pickString(merged.subtitle_url, mdOutputs.subtitle_url, mpOutputs.subtitle_url);
  const subtitleAssUrl = pickString(merged.subtitle_ass_url, mdOutputs.subtitle_ass_url, mpOutputs.subtitle_ass_url);
  const audioUrl = pickString(merged.audio_url, mdOutputs.audio_url, mpOutputs.audio_url);
  const manifestUrl = pickString(merged.manifest_url, mdOutputs.manifest_url, mpOutputs.manifest_url);
  const manifestFallback = merged.manifest_json ?? mdOutputs.manifest_json ?? manifestPreview ?? merged;

  return { videoUrl, subtitleUrl, subtitleAssUrl, audioUrl, manifestUrl, manifestFallback };
}

function shouldStopPolling(task: TaskRecord | null): boolean {
  const status = String(task?.status || "").toLowerCase();
  const stage = String(task?.stage || "").toLowerCase();
  const metadata = asRecord(task?.metadata);
  const outputs = asRecord(metadata.outputs);
  const manifestPreviewOutputs = asRecord(asRecord(metadata.manifest_preview).outputs);
  const hasVideo =
    typeof outputs.video_url === "string" ||
    typeof manifestPreviewOutputs.video_url === "string" ||
    typeof task?.output_url === "string";
  return (
    TERMINAL_STATUSES.has(status) ||
    stage === "done" ||
    stage === "failed" ||
    hasVideo
  );
}

export default function LocalizationClient() {
  const [mode, setMode] = useState<Mode>("baseline");
  const [activeTopTab, setActiveTopTab] = useState<TopTab>("playground");
  const [activeOutputTab, setActiveOutputTab] = useState<OutputTab>("video");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [inputVideoUrl, setInputVideoUrl] = useState<string | null>(null);
  const [targetLang, setTargetLang] = useState("my");
  const [voiceId, setVoiceId] = useState("mm_female_1");
  const [subtitleMode, setSubtitleMode] = useState<"sidecar" | "burned">("burned");
  const [audioStrategy, setAudioStrategy] = useState<AudioStrategy>("mute_original");
  const [dubGain, setDubGain] = useState(1.0);
  const [bgmGain, setBgmGain] = useState(0.28);
  const [voiceSpeed, setVoiceSpeed] = useState(1.0);
  const [lipsyncEnabled, setLipsyncEnabled] = useState(false);
  const [lipsyncScope, setLipsyncScope] = useState<"face" | "full">("face");

  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const pollTokenRef = useRef(0);
  const startedAtRef = useRef<number>(0);

  const taskId = task?.task_id || "";
  const logs = useMemo(() => task?.logs || [], [task?.logs]);
  const extracted = useMemo(() => extractLocalizationOutputs(task), [task]);
  const videoUrl = resolveAssetUrl(extracted.videoUrl);
  const subtitleUrl = resolveAssetUrl(extracted.subtitleUrl);
  const subtitleAssUrl = resolveAssetUrl(extracted.subtitleAssUrl);
  const audioUrl = resolveAssetUrl(extracted.audioUrl);
  const manifestUrl = resolveAssetUrl(extracted.manifestUrl);
  const manifestFallback = extracted.manifestFallback;

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

  useEffect(() => {
    if (mode === "baseline") {
      setLipsyncEnabled(false);
    }
  }, [mode]);

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

    const effectiveLipsyncEnabled = mode === "intelligent" ? lipsyncEnabled : false;

    setError(null);
    setWarning(null);
    setTask(null);
    setIsRunning(true);

    try {
      const inputKey = await uploadFileToR2(videoFile);
      const res = await createTask({
        service_type: "localization",
        mode,
        input_key: inputKey,
        inputs: {
          target_lang: targetLang,
          voice_id: voiceId,
          subtitle_mode: subtitleMode,
          audio_strategy: audioStrategy,
          preserve_bgm: audioStrategy !== "mute_original",
          ducking: audioStrategy === "duck_original",
          dub_gain: dubGain,
          bgm_gain: bgmGain,
          voice_speed: voiceSpeed,
          lipsync_enabled: effectiveLipsyncEnabled,
          lipsync_scope: effectiveLipsyncEnabled ? lipsyncScope : undefined,
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

  const requestPreview = {
    service_type: "localization",
    mode,
    input_key: videoFile ? "(uploaded key)" : "",
    inputs: {
      target_lang: targetLang,
      voice_id: voiceId,
      subtitle_mode: subtitleMode,
      audio_strategy: audioStrategy,
      preserve_bgm: audioStrategy !== "mute_original",
      ducking: audioStrategy === "duck_original",
      dub_gain: dubGain,
      bgm_gain: bgmGain,
      voice_speed: voiceSpeed,
      lipsync_enabled: mode === "intelligent" ? lipsyncEnabled : false,
      lipsync_scope: mode === "intelligent" && lipsyncEnabled ? lipsyncScope : null,
    },
    workflow_config: {
      preset: mode,
    },
  };

  const payloadPreview = {
    request: requestPreview,
    task: task || null,
  };

  const apiBase = (process.env.NEXT_PUBLIC_API_BASE || "https://swiftcraft.ai").replace(/\/+$/, "");
  const curlSnippet = [
    `curl -X POST \"${apiBase}/api/v1/tasks\"`,
    "  -H \"Content-Type: application/json\"",
    `  -d '${JSON.stringify(requestPreview)}'`,
  ].join(" \\\n");

  return (
    <div className="h-screen bg-white text-slate-900 flex flex-col font-sans overflow-hidden">
      <nav className="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0 z-20 shadow-sm">
        <div className="flex items-center gap-4">
          <Link href="/" className="font-bold text-slate-900 tracking-tight hover:text-blue-600 transition">
            SwiftCraft
          </Link>
          <span className="text-slate-300">/</span>
          <span className="font-medium text-slate-600">Localization</span>
        </div>

        <div className="bg-slate-100 p-1 rounded-lg border border-slate-200 flex relative">
          <button
            onClick={() => setMode("baseline")}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === "baseline"
                ? "bg-white text-slate-900 shadow-sm border border-slate-200 ring-1 ring-black/5"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Baseline
          </button>
          <button
            onClick={() => setMode("intelligent")}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === "intelligent"
                ? "bg-white text-blue-600 shadow-sm border border-slate-200 ring-1 ring-black/5"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Intelligent
          </button>
        </div>

        <div className="text-xs font-medium text-slate-500 bg-slate-50 px-2 py-1 rounded border border-slate-200">
          v1.6 Demo
        </div>
      </nav>

      <div className="flex-1 flex overflow-hidden">
        <InputPanel
          mode={mode}
          activeTopTab={activeTopTab}
          setActiveTopTab={setActiveTopTab}
          videoFile={videoFile}
          setVideoFile={setVideoFile}
          inputVideoUrl={inputVideoUrl}
          targetLang={targetLang}
          setTargetLang={setTargetLang}
          voiceId={voiceId}
          setVoiceId={setVoiceId}
          subtitleMode={subtitleMode}
          setSubtitleMode={setSubtitleMode}
          audioStrategy={audioStrategy}
          setAudioStrategy={setAudioStrategy}
          dubGain={dubGain}
          setDubGain={setDubGain}
          bgmGain={bgmGain}
          setBgmGain={setBgmGain}
          voiceSpeed={voiceSpeed}
          setVoiceSpeed={setVoiceSpeed}
          lipsyncEnabled={lipsyncEnabled}
          setLipsyncEnabled={setLipsyncEnabled}
          lipsyncScope={lipsyncScope}
          setLipsyncScope={setLipsyncScope}
          isRunning={isRunning}
          payloadPreview={payloadPreview}
          curlSnippet={curlSnippet}
          onRun={handleRun}
        />

        <div className="flex-1 bg-slate-50/80 p-8 overflow-y-auto">
          <div className="w-full max-w-6xl mx-auto space-y-4">
            {taskId ? (
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                Task ID: {taskId}
              </div>
            ) : null}
            {warning ? (
              <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{warning}</div>
            ) : null}
            {error ? (
              <div className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</div>
            ) : null}
            {hasPolicyViolation ? (
              <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Safety checker blocked this content. Try safer reference material or prompt text.
              </div>
            ) : null}

            <div className="w-full bg-black rounded-2xl shadow-2xl border border-slate-300/50 overflow-hidden max-w-5xl aspect-video">
              {videoUrl ? (
                <video controls className="w-full h-full object-contain" src={videoUrl} />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-slate-300">
                  Waiting for localized video output
                </div>
              )}
            </div>

            <OutputTabs
              activeTab={activeOutputTab}
              setActiveTab={setActiveOutputTab}
              videoUrl={videoUrl}
              subtitleUrl={subtitleUrl}
              subtitleAssUrl={subtitleAssUrl}
              audioUrl={audioUrl}
              manifestUrl={manifestUrl}
              manifestFallback={manifestFallback}
            />

            <StagePanel mode={mode} stage={String(task?.stage || "")} status={String(task?.status || "")} />
            <LogsPanel status={String(task?.status || "")} stage={String(task?.stage || "")} logs={logs} />
          </div>
        </div>
      </div>
    </div>
  );
}
