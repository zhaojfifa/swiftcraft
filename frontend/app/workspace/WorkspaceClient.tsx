"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { UploadCloud, Play, Terminal, CircleHelp } from "lucide-react";
import Link from "next/link";

import { ApiHttpError, createTask, getTask, getUploadUrl, TaskRecord } from "../../lib/api";
import { SwapMode, resolvePresetInputKey } from "../../lib/presets";
import { SERVICE_REGISTRY } from "../../lib/services/registry";
import { resolveAssetUrl } from "../../lib/url";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "done"]);
const POLL_FAST_ATTEMPTS = 10;
const POLL_FAST_MS = 1000;
const POLL_MAX_MS = 30000;
const POLL_STILL_PROCESSING_MS = 180000;
const POLL_STUCK_MAX_UNCHANGED = 8;

function shouldStopPolling(current: TaskRecord | null) {
  const status = (current?.status || "").toLowerCase();
  const stage = (current?.stage || "").toLowerCase();
  return (
    Boolean(current?.output_url || current?.output_key) ||
    TERMINAL_STATUSES.has(status) ||
    stage === "done" ||
    stage === "failed"
  );
}

export default function WorkspaceClient() {
  const searchParams = useSearchParams();
  const serviceType = (searchParams.get("service") || "swap").toLowerCase();
  const serviceConfig =
    SERVICE_REGISTRY.find((service) => service.id === serviceType) ?? SERVICE_REGISTRY[0];
  const isSwap = serviceConfig.id === "swap";
  const isAvatar = serviceConfig.id === "avatar";
  const isLocalization = serviceConfig.id === "localization";

  const [mode, setMode] = useState<SwapMode>("intelligent");
  const [inputSource, setInputSource] = useState<"preset" | "upload">("preset");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [inputVideoUrl, setInputVideoUrl] = useState<string | null>(null);
  const [inputImageUrl, setInputImageUrl] = useState<string | null>(null);
  const [faceEnhancer, setFaceEnhancer] = useState(true);
  const [orientation, setOrientation] = useState<"front" | "side" | "back">("front");
  const [prompt, setPrompt] = useState<string>("");
  const [showPromptTips, setShowPromptTips] = useState(false);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uiPollingWarning, setUiPollingWarning] = useState<string | null>(null);
  const [uiLogs, setUiLogs] = useState<string[]>([]);
  const [isPollingPaused, setIsPollingPaused] = useState(false);
  const [activeTab, setActiveTab] = useState<"playground" | "json" | "api">("playground");
  const pollRef = useRef<number | null>(null);
  const pollTokenRef = useRef(0);
  const cancelPolling = () => {
    if (pollRef.current) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    pollTokenRef.current += 1;
  };

  useEffect(() => {
    return () => {
      cancelPolling();
    };
  }, []);

  useEffect(() => {
    if (!isSwap) {
      setInputSource("upload");
    }
  }, [isSwap]);

  useEffect(() => {
    if (!videoFile) {
      setInputVideoUrl(null);
      return;
    }
    cancelPolling();
    setIsRunning(false);
    setTask(null);
    const previewUrl = URL.createObjectURL(videoFile);
    setInputVideoUrl(previewUrl);
    return () => {
      URL.revokeObjectURL(previewUrl);
    };
  }, [videoFile]);

  useEffect(() => {
    if (!imageFile) {
      setInputImageUrl(null);
      return;
    }
    const previewUrl = URL.createObjectURL(imageFile);
    setInputImageUrl(previewUrl);
    return () => {
      URL.revokeObjectURL(previewUrl);
    };
  }, [imageFile]);

  const serviceApi = String(serviceType || "swap").toLowerCase();
  const modeApi = String(mode || "baseline").toLowerCase() as SwapMode;
  const presetKey = resolvePresetInputKey(serviceApi, modeApi);
  const cdnBase = (process.env.NEXT_PUBLIC_CDN_BASE_URL || "").replace(/\/+$/, "");
  const safeDemoMotionKey = (process.env.NEXT_PUBLIC_SAFE_DEMO_MOTION_KEY || "").trim();
  const safeDemoCharacterKey = (process.env.NEXT_PUBLIC_SAFE_DEMO_CHARACTER_KEY || "").trim();

  const uploadFileToR2 = async (file: File) => {
    const contentType = file.type || "application/octet-stream";
    const upload = await getUploadUrl({
      filename: file.name,
      content_type: contentType,
      purpose: "uploads"
    });
    const putRes = await fetch(upload.upload_url, {
      method: "PUT",
      headers: upload.headers,
      body: file
    });
    if (!putRes.ok) {
      throw new Error(`Upload failed (HTTP ${putRes.status})`);
    }
    return upload.file_key;
  };

  const getPollDelayMs = (attempt: number) => {
    if (attempt < POLL_FAST_ATTEMPTS) return POLL_FAST_MS;
    const backoffIndex = attempt - POLL_FAST_ATTEMPTS;
    return Math.min(POLL_MAX_MS, 2000 * Math.pow(2, backoffIndex));
  };

  const fetchTaskFromCdn = async (taskId: string): Promise<TaskRecord> => {
    const cdnUrl = `https://cdn.swiftcraft.ai/tasks/${encodeURIComponent(taskId)}.json`;
    const res = await fetch(cdnUrl, { method: "GET", cache: "no-store" });
    if (!res.ok) {
      throw new ApiHttpError(`cdn getTask failed (${res.status})`, res.status);
    }
    const text = await res.text();
    if (!text) {
      throw new Error("CDN task response is empty");
    }
    try {
      return JSON.parse(text) as TaskRecord;
    } catch {
      throw new Error("CDN task response is not valid JSON");
    }
  };

  const startTaskPolling = (taskId: string) => {
    let attempt = 0;
    const startedAt = Date.now();
    let unchangedPollCount = 0;
    let lastTaskSignature = "";
    let stuckMode = false;
    pollTokenRef.current += 1;
    const token = pollTokenRef.current;
    setIsPollingPaused(false);
    setUiLogs((prev) => [...prev, `[ui] polling_base=cdn_ssot_first task_id=${taskId}`]);

    if (pollRef.current) {
      window.clearTimeout(pollRef.current);
    }

    const scheduleNext = () => {
      const delay = getPollDelayMs(attempt);
      setUiLogs((prev) => [...prev, `[ui] poll_interval_ms=${delay} attempt=${attempt + 1}`]);
      attempt += 1;
      pollRef.current = window.setTimeout(tick, delay);
    };

    const tick = async () => {
      if (pollTokenRef.current !== token) return;
      if (document.visibilityState !== "visible") {
        scheduleNext();
        return;
      }
      try {
        let latest: TaskRecord | null = null;
        try {
          latest = await fetchTaskFromCdn(taskId);
        } catch (cdnErr) {
          const cdnStatus = cdnErr instanceof ApiHttpError ? cdnErr.status : undefined;
          if (cdnStatus === 404) {
            setUiPollingWarning("Task record not yet available, retrying...");
          }
          try {
            latest = await getTask(taskId);
            if (cdnStatus === 404) {
              setUiPollingWarning("Task record not yet available, retrying...");
            }
          } catch (apiErr) {
            const apiStatus = apiErr instanceof ApiHttpError ? apiErr.status : undefined;
            if (apiStatus === 502 || apiStatus === 503) {
              if (!stuckMode) {
                setUiPollingWarning(
                  `Temporary gateway issue (HTTP ${apiStatus}), retrying. Long-running tasks may take several minutes.`,
                );
              }
              setUiLogs((prev) => [...prev, `[ui] last_error=http_${apiStatus} source=api_fallback`]);
              scheduleNext();
              return;
            }
            if (apiStatus === 404) {
              setUiPollingWarning("Task record not yet available, retrying...");
              scheduleNext();
              return;
            }
            throw apiErr;
          }
        }

        if (!latest) {
          scheduleNext();
          return;
        }

        setTask(latest);
        const signature = [
          String(latest.status || ""),
          String(latest.stage || ""),
          String(latest.progress ?? ""),
          String(Array.isArray(latest.logs) ? latest.logs.length : 0),
          String(latest.output_url || ""),
        ].join("|");
        if (signature === lastTaskSignature) {
          unchangedPollCount += 1;
        } else {
          unchangedPollCount = 0;
          lastTaskSignature = signature;
          stuckMode = false;
        }
        if ((latest.status || "").toLowerCase() === "failed") {
          const lastLog =
            Array.isArray(latest.logs) && latest.logs.length > 0
              ? latest.logs[latest.logs.length - 1]
              : null;
          setError(
            latest.error ||
              lastLog ||
              "Task failed.",
          );
        } else {
          setError(null);
        }

        const elapsed = Date.now() - startedAt;
        if (elapsed > POLL_STILL_PROCESSING_MS && !shouldStopPolling(latest)) {
          setUiPollingWarning("Still processing, you can keep this tab open or refresh later.");
        } else if (!shouldStopPolling(latest) && unchangedPollCount >= POLL_STUCK_MAX_UNCHANGED) {
          stuckMode = true;
          setUiPollingWarning("Still processing... (backend confirmed)");
        } else if (!uiPollingWarning?.startsWith("Task record not yet available")) {
          setUiPollingWarning(null);
        }

        setIsPollingPaused(false);
        if (shouldStopPolling(latest)) {
          setIsRunning(false);
          if (pollRef.current) {
            window.clearTimeout(pollRef.current);
            pollRef.current = null;
          }
          return;
        }
      } catch (err) {
        const name = (err as { name?: string } | null)?.name || "";
        if (name === "AbortError") {
          scheduleNext();
          return;
        }

        const status = err instanceof ApiHttpError ? err.status : undefined;
        if (status === 404) {
          setUiPollingWarning("Task record not yet available, retrying...");
          scheduleNext();
          return;
        }

        if (status === 502 || status === 503) {
          if (!stuckMode) {
            setUiPollingWarning(
              `Temporary gateway issue (HTTP ${status}), retrying. Long-running tasks may take several minutes.`,
            );
          }
          setUiLogs((prev) => [...prev, `[ui] last_error=http_${status} source=polling`]);
          scheduleNext();
          return;
        }

        if (status !== undefined) {
          setUiLogs((prev) => [...prev, `[ui] last_error=http_${status} source=polling_terminal`]);
          setError(err instanceof Error ? err.message : "Polling failed unexpectedly.");
          setIsPollingPaused(true);
          setIsRunning(false);
          if (pollRef.current) {
            window.clearTimeout(pollRef.current);
            pollRef.current = null;
          }
          return;
        }

        setUiLogs((prev) => [...prev, `[ui] last_error=network_or_parse source=polling`]);
        setUiPollingWarning("Task record not yet available, retrying...");
        scheduleNext();
        return;
      }

      scheduleNext();
    };

    tick();
  };

  const handleRetryPolling = () => {
    if (!taskId) return;
    setError(null);
    setUiPollingWarning(null);
    setIsPollingPaused(false);
    setUiLogs([]);
    startTaskPolling(taskId);
  };

  const runAvatarTask = async (overrides?: { motionKey?: string; characterKey?: string; modeOverride?: SwapMode }) => {
    const characterKey = overrides?.characterKey || (await uploadFileToR2(imageFile as File));
    const motionKey = overrides?.motionKey || (await uploadFileToR2(videoFile as File));
    return createTask({
      service_type: "avatar_transfer",
      model_id: "kling-v2.6-std-motion",
      mode: overrides?.modeOverride || modeApi,
      input_key: motionKey,
      inputs: {
        character_image: characterKey,
        motion_video: motionKey,
        character_orientation: orientation,
        prompt: prompt.trim() ? prompt.trim() : undefined
      }
    });
  };

  const handleRun = async () => {
    cancelPolling();
    if (isLocalization) {
      return;
    }
    if (inputSource === "upload" && !videoFile) {
      setError("Please upload a source video for upload mode.");
      return;
    }
    if (isAvatar && (!imageFile || !videoFile)) {
      setError("Please upload a character image and motion video for avatar mode.");
      return;
    }
    setError(null);
    setUiPollingWarning(null);
    setIsPollingPaused(false);
    setUiLogs([]);
    setIsRunning(true);
    setTask(null);

    try {
      let result;
      if (isAvatar) {
        result = await runAvatarTask();
      } else {
        const input_key =
          inputSource === "preset" ? presetKey : await uploadFileToR2(videoFile as File);
        result = await createTask({
          service: serviceApi,
          mode: modeApi,
          input_key
        });
      }
      startTaskPolling(result.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Failed to start task.");
    }
  };

  const outputUrl =
    resolveAssetUrl(task?.output_url ?? null) ??
    (task?.output_key && cdnBase ? `${cdnBase}/${task.output_key}` : null);
  // Preview priority: output -> local upload (upload mode) -> empty placeholder.
  const previewUrl = outputUrl ?? (inputSource === "upload" ? inputVideoUrl : null);
  const logs = [...uiLogs, ...(task?.logs ?? [])];
  const taskId = task?.task_id ?? task?.id ?? "";
  const showUploadBlocks = (isSwap && inputSource === "upload") || isAvatar;
  const lowerError = (task?.error || "").toLowerCase();
  const hasPolicyViolation =
    lowerError.includes("content_policy_violation") ||
    logs.some((line) => line.toLowerCase().includes("content_policy_violation"));
  const showPolicyPanel = isAvatar && modeApi === "intelligent" && hasPolicyViolation;
  const failedReason =
    (task?.status || "").toLowerCase() === "failed"
      ? task?.error || logs[logs.length - 1] || "Task failed."
      : null;
  const canUseSafeDemo = Boolean(safeDemoMotionKey && safeDemoCharacterKey) && !isRunning;
  const canRun = isLocalization
    ? false
    : isAvatar
      ? Boolean(videoFile && imageFile) && !isRunning
      : (inputSource === "preset" || Boolean(videoFile)) && !isRunning;
  const payloadPreview = isAvatar
    ? {
        service_type: "avatar_transfer",
        model_id: "kling-v2.6-std-motion",
        mode: modeApi,
        input_key: "(motion key)",
        inputs: {
          character_image: "(character key)",
          motion_video: "(motion key)",
          character_orientation: orientation,
          prompt: prompt ? "(optional)" : ""
        }
      }
    : isLocalization
      ? { service: serviceApi, mode: modeApi, preview: true }
      : {
          service: serviceApi,
          mode: modeApi,
          input_key: inputSource === "preset" ? presetKey : "(uploaded key)",
          source: inputSource
        };
  const jsonPreview = {
    request: payloadPreview,
    task: task ?? null
  };
  const apiBase = (process.env.NEXT_PUBLIC_API_BASE || "https://swiftcraft.ai").replace(/\/+$/, "");
  const curlSnippet = isAvatar
    ? [
        `curl -X POST \"${apiBase}/api/v1/tasks\"`,
        "  -H \"Content-Type: application/json\"",
        `  -d '{\"service_type\":\"avatar_transfer\",\"model_id\":\"kling-v2.6-std-motion\",\"mode\":\"${modeApi}\",\"input_key\":\"<motion_key>\",\"inputs\":{\"character_image\":\"<character_key>\",\"motion_video\":\"<motion_key>\",\"character_orientation\":\"${orientation}\"}}'`
      ].join(" \\\n")
    : [
        `curl -X POST \"${apiBase}/api/v1/tasks\"`,
        "  -H \"Content-Type: application/json\"",
        `  -d '{\"service\":\"${serviceApi}\",\"mode\":\"${modeApi}\",\"input_key\":\"${presetKey}\"}'`
      ].join(" \\\n");

  const handleRetrySafeSlicing = async () => {
    if (!isAvatar || isRunning) return;
    if (!videoFile || !imageFile) {
      setError("Retry requires character image and motion video files.");
      return;
    }
    setError(null);
    setUiPollingWarning(null);
    setIsPollingPaused(false);
    setUiLogs([]);
    setIsRunning(true);
    setTask(null);
    cancelPolling();
    try {
      const result = await runAvatarTask({ modeOverride: "intelligent" });
      startTaskPolling(result.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Retry failed.");
    }
  };

  const handleUseSafeDemoClip = async () => {
    if (!isAvatar || isRunning || !safeDemoMotionKey || !safeDemoCharacterKey) return;
    setError(null);
    setUiPollingWarning(null);
    setIsPollingPaused(false);
    setUiLogs([]);
    setIsRunning(true);
    setTask(null);
    cancelPolling();
    try {
      const result = await runAvatarTask({
        motionKey: safeDemoMotionKey,
        characterKey: safeDemoCharacterKey,
        modeOverride: "intelligent"
      });
      startTaskPolling(result.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Safe demo retry failed.");
    }
  };

  return (
    <div className="h-screen bg-white text-slate-900 flex flex-col font-sans overflow-hidden">
      <nav className="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0 z-20 shadow-sm">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="font-bold text-slate-900 tracking-tight hover:text-blue-600 transition"
          >
            SwiftCraft
          </Link>
          <span className="text-slate-300">/</span>
          <span className="font-medium text-slate-600 capitalize">{serviceType}</span>
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
        <div className="w-[400px] bg-white border-r border-slate-200 flex flex-col z-10 shadow-[4px_0_24px_rgba(0,0,0,0.02)]">
          <div className="flex border-b border-slate-100 px-6 pt-6 gap-6 text-sm">
            <button
              className={`pb-3 transition ${
                activeTab === "playground"
                  ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
              onClick={() => setActiveTab("playground")}
            >
              Playground
            </button>
            <button
              className={`pb-3 transition ${
                activeTab === "json"
                  ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
              onClick={() => setActiveTab("json")}
            >
              JSON
            </button>
            <button
              className={`pb-3 transition ${
                activeTab === "api"
                  ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
              onClick={() => setActiveTab("api")}
            >
              API
            </button>
          </div>

          <div className="p-6 space-y-8 overflow-y-auto flex-1">
            {activeTab !== "playground" ? (
              activeTab === "json" ? (
                <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap">
                  {JSON.stringify(jsonPreview, null, 2)}
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap">
                    {curlSnippet}
                  </div>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(curlSnippet)}
                    className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
                  >
                    Copy curl
                  </button>
                </div>
              )
            ) : null}
            {activeTab === "playground" ? (
              <div className="space-y-8">
                {isLocalization ? (
                  <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-600">
                    <div className="text-sm font-semibold text-slate-900">
                      Video Localization (Preview)
                    </div>
                    <div className="text-xs text-slate-500 mt-2">
                      Coming soon. This is a placeholder for future expansion.
                    </div>
                  </div>
                ) : null}
                {isSwap ? (
                  <div className="space-y-3">
                    <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                      Input Source
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setInputSource("preset")}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                          inputSource === "preset"
                            ? "border-blue-600 text-blue-600 bg-blue-50"
                            : "border-slate-200 text-slate-500 bg-white"
                        }`}
                      >
                        Preset
                      </button>
                      <button
                        type="button"
                        onClick={() => setInputSource("upload")}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                          inputSource === "upload"
                            ? "border-blue-600 text-blue-600 bg-blue-50"
                            : "border-slate-200 text-slate-500 bg-white"
                        }`}
                      >
                        Upload
                      </button>
                    </div>
                    {inputSource === "preset" ? (
                      <div className="text-xs text-slate-500">
                        Using preset input_key: <span className="font-mono">{presetKey}</span>
                      </div>
                    ) : (
                      <div className="text-xs text-slate-500">Upload a source video to generate input_key.</div>
                    )}
                  </div>
                ) : null}
                {showUploadBlocks ? (
                  <>
                    {isAvatar ? (
                      <>
                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Character Image
                          </label>
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span>{imageFile ? imageFile.name : "No file selected"}</span>
                            {imageFile ? (
                              <button
                                type="button"
                                className="text-slate-500 hover:text-slate-700"
                                onClick={() => setImageFile(null)}
                              >
                                Clear
                              </button>
                            ) : null}
                          </div>
                          <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                            <div className="relative flex flex-col items-center justify-center">
                              <input
                                type="file"
                                accept="image/*"
                                onChange={(event) => setImageFile(event.target.files?.[0] || null)}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                              <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                                <UploadCloud className="w-5 h-5 text-slate-600" />
                              </div>
                              <span className="text-xs font-medium text-slate-600">Click to upload image</span>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2">
                              {inputImageUrl ? (
                                <div className="flex items-center gap-3">
                                  <img
                                    src={inputImageUrl}
                                    alt="Character preview"
                                    className="h-10 w-10 rounded-md object-cover"
                                  />
                                  <span className="text-[11px] text-slate-500">Character preview ready</span>
                                </div>
                              ) : (
                                <div className="flex items-center justify-center text-[11px] text-slate-400">
                                  Preview will appear here
                                </div>
                              )}
                            </div>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider flex justify-between">
                            Motion Reference (Video)
                            <span className="text-[10px] font-normal text-slate-400">MP4, 4-8s</span>
                          </label>
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span>{videoFile ? videoFile.name : "No file selected"}</span>
                            {videoFile ? (
                              <button
                                type="button"
                                className="text-slate-500 hover:text-slate-700"
                                onClick={() => setVideoFile(null)}
                              >
                                Clear
                              </button>
                            ) : null}
                          </div>
                          <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                            <div className="relative flex flex-col items-center justify-center">
                              <input
                                type="file"
                                accept="video/*"
                                onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                              <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                                <UploadCloud className="w-5 h-5 text-slate-600" />
                              </div>
                              <span className="text-xs font-medium text-slate-600">Click to upload video</span>
                              <span className="text-[10px] text-slate-400 mt-1">or drag and drop</span>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2">
                              {inputVideoUrl ? (
                                <div className="flex items-center gap-3">
                                  <video
                                    src={inputVideoUrl}
                                    muted
                                    playsInline
                                    className="h-10 w-14 rounded-md object-cover"
                                  />
                                  <span className="text-[11px] text-slate-500">Video preview ready</span>
                                </div>
                              ) : (
                                <div className="flex items-center justify-center text-[11px] text-slate-400">
                                  Preview will appear here
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider flex justify-between">
                            Source Video
                            <span className="text-[10px] font-normal text-slate-400">MP4, 4-8s</span>
                          </label>
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span>{videoFile ? videoFile.name : "No file selected"}</span>
                            {videoFile ? (
                              <button
                                type="button"
                                className="text-slate-500 hover:text-slate-700"
                                onClick={() => setVideoFile(null)}
                              >
                                Clear
                              </button>
                            ) : null}
                          </div>
                          <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                            <div className="relative flex flex-col items-center justify-center">
                              <input
                                type="file"
                                accept="video/*"
                                onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                              <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                                <UploadCloud className="w-5 h-5 text-slate-600" />
                              </div>
                              <span className="text-xs font-medium text-slate-600">Click to upload video</span>
                              <span className="text-[10px] text-slate-400 mt-1">or drag and drop</span>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2">
                              {inputVideoUrl ? (
                                <div className="flex items-center gap-3">
                                  <video
                                    src={inputVideoUrl}
                                    muted
                                    playsInline
                                    className="h-10 w-14 rounded-md object-cover"
                                  />
                                  <span className="text-[11px] text-slate-500">Video preview ready</span>
                                </div>
                              ) : (
                                <div className="flex items-center justify-center text-[11px] text-slate-400">
                                  Preview will appear here
                                </div>
                              )}
                            </div>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Target Face
                          </label>
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span>{imageFile ? imageFile.name : "No file selected"}</span>
                            {imageFile ? (
                              <button
                                type="button"
                                className="text-slate-500 hover:text-slate-700"
                                onClick={() => setImageFile(null)}
                              >
                                Clear
                              </button>
                            ) : null}
                          </div>
                          <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                            <div className="relative flex flex-col items-center justify-center">
                              <input
                                type="file"
                                accept="image/*"
                                onChange={(event) => setImageFile(event.target.files?.[0] || null)}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                              <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                                <UploadCloud className="w-5 h-5 text-slate-600" />
                              </div>
                              <span className="text-xs font-medium text-slate-600">Click to upload image</span>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2">
                              {inputImageUrl ? (
                                <div className="flex items-center gap-3">
                                  <img
                                    src={inputImageUrl}
                                    alt="Target preview"
                                    className="h-10 w-10 rounded-md object-cover"
                                  />
                                  <span className="text-[11px] text-slate-500">Target preview ready</span>
                                </div>
                              ) : (
                                <div className="flex items-center justify-center text-[11px] text-slate-400">
                                  Preview will appear here
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </>
                    )}

                    {isAvatar ? (
                      <>
                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Orientation
                          </label>
                          <select
                            value={orientation}
                            onChange={(event) =>
                              setOrientation(event.target.value as "front" | "side" | "back")
                            }
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="front">Front</option>
                            <option value="side">Side</option>
                            <option value="back">Back</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <div className="flex items-center justify-between">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                              Prompt
                            </label>
                            <button
                              type="button"
                              onClick={() => setShowPromptTips((prev) => !prev)}
                              className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-500 hover:text-slate-700"
                            >
                              <CircleHelp className="h-3.5 w-3.5" />
                              Tips
                            </button>
                          </div>
                          {showPromptTips ? (
                            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-600 space-y-2">
                              <div className="font-semibold text-slate-700">Safe prompt templates</div>
                              <button
                                type="button"
                                onClick={() => setPrompt("Clean studio portrait motion, natural expression, stable lighting, no sensitive content.")}
                                className="block w-full rounded border border-slate-200 bg-white px-2 py-1 text-left hover:bg-slate-100"
                              >
                                Clean studio portrait motion, natural expression, stable lighting.
                              </button>
                              <button
                                type="button"
                                onClick={() => setPrompt("Walking in a city street, cinematic but neutral style, calm pace, family friendly.")}
                                className="block w-full rounded border border-slate-200 bg-white px-2 py-1 text-left hover:bg-slate-100"
                              >
                                Walking in a city street, cinematic neutral style, family friendly.
                              </button>
                              <button
                                type="button"
                                onClick={() => setPrompt("Dancing in a bright room, energetic yet safe, no explicit themes, no risky actions.")}
                                className="block w-full rounded border border-slate-200 bg-white px-2 py-1 text-left hover:bg-slate-100"
                              >
                                Dancing in a bright room, energetic and safe, no risky actions.
                              </button>
                            </div>
                          ) : null}
                          <input
                            type="text"
                            value={prompt}
                            onChange={(event) => setPrompt(event.target.value)}
                            placeholder="Optional prompt for style or motion"
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400"
                          />
                        </div>
                      </>
                    ) : null}

                    {isSwap && inputSource === "upload" ? (
                      <div className="pt-6 border-t border-slate-100">
                        <div className="flex justify-between items-center py-2">
                          <span className="text-sm font-medium text-slate-700">Face Enhancer</span>
                          <button
                            type="button"
                            onClick={() => setFaceEnhancer((prev) => !prev)}
                            className={`w-10 h-6 rounded-full relative cursor-pointer shadow-inner ${
                              faceEnhancer ? "bg-blue-600" : "bg-slate-300"
                            }`}
                          >
                            <div
                              className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow-sm transition-all ${
                                faceEnhancer ? "right-1" : "left-1"
                              }`}
                            ></div>
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : null}
                {error ? <p className="text-xs text-rose-500">{error}</p> : null}
                {failedReason ? <p className="text-xs text-rose-500">Failure reason: {failedReason}</p> : null}
                {uiPollingWarning ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 space-y-2">
                    <div>{uiPollingWarning}</div>
                    {isPollingPaused ? (
                      <button
                        type="button"
                        onClick={handleRetryPolling}
                        className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-[11px] font-semibold text-blue-700"
                      >
                        Retry polling
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {showPolicyPanel ? (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 space-y-2">
                    <div className="font-semibold">Safety policy blocked this input.</div>
                    <div>
                      Safety checker blocked this request. Try safe slicing, use a safe reference video, or remove sensitive content.
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleRetrySafeSlicing}
                        disabled={isRunning}
                        className="rounded-md border border-amber-400 bg-white px-3 py-1.5 text-[11px] font-semibold text-amber-800 disabled:opacity-60"
                      >
                        Retry (safe slicing)
                      </button>
                      {canUseSafeDemo ? (
                        <button
                          type="button"
                          onClick={handleUseSafeDemoClip}
                          disabled={isRunning}
                          className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-[11px] font-semibold text-blue-700 disabled:opacity-60"
                        >
                          Use Safe Reference Video
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {taskId ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                    <div>Task ID: {taskId}</div>
                    <div>
                      Status: {task?.status || "queued"} · Stage: {task?.stage || "queued"}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="p-6 border-t border-slate-100 bg-white">
            <button
              onClick={handleRun}
              disabled={!canRun}
              className={`w-full py-3.5 rounded-xl font-bold text-white flex items-center justify-center gap-2 shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all ${
                mode === "intelligent"
                  ? "bg-blue-600 hover:bg-blue-700 shadow-blue-200"
                  : "bg-slate-800 hover:bg-slate-900 shadow-slate-200"
              } ${!canRun ? "opacity-60 cursor-not-allowed" : ""}`}
            >
              <Play className="w-4 h-4" />
              {isLocalization
                ? "Preview"
                : isRunning
                  ? "Running..."
                  : `Run ${mode === "intelligent" ? "SwiftFlow" : "Basic"}`}
            </button>
            <div className="text-center mt-3 text-[10px] text-slate-400 font-medium">
              Estimated Cost: {mode === "intelligent" ? "$0.15" : "$0.05"}
            </div>
          </div>
        </div>

        <div className="flex-1 bg-slate-50/80 p-10 flex flex-col items-center justify-center relative">
          <div
            className="absolute inset-0 opacity-[0.05] pointer-events-none"
            style={{
              backgroundImage: "radial-gradient(#475569 1px, transparent 1px)",
              backgroundSize: "24px 24px"
            }}
          ></div>

          <div
            className={`w-full bg-black rounded-2xl shadow-2xl border border-slate-300/50 flex flex-col items-center justify-center relative overflow-hidden group ${
              isAvatar ? "max-w-md aspect-[9/16]" : "max-w-4xl aspect-video"
            }`}
          >
            {previewUrl ? (
              <video controls className="w-full h-full object-contain" src={previewUrl} />
            ) : (
              <div className="text-slate-500 font-medium flex flex-col items-center gap-3">
                <div className="w-16 h-16 rounded-full bg-slate-800/50 flex items-center justify-center backdrop-blur">
                  <Play className="w-6 h-6 text-slate-400 ml-1" />
                </div>
                Output Preview
              </div>
            )}
          </div>

          <div className="w-full max-w-4xl mt-6 animate-in slide-in-from-bottom-4 fade-in duration-500">
            <div className="flex items-center gap-2 mb-2 ml-1">
              <Terminal className="w-3 h-3 text-slate-400" />
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                SwiftFlow Engine Logs
              </span>
            </div>
            <div className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm font-mono text-xs h-32 overflow-y-auto">
              {logs.length ? (
                logs.map((line, index) => (
                  <div key={`${line}-${index}`} className="flex gap-3 py-1 border-b border-slate-50">
                    <span className="text-slate-400 w-12 select-none">
                      [{String(index + 1).padStart(2, "0")}]
                    </span>
                    <span
                      className={
                        line.startsWith("[slice]") || line.startsWith("[safety]") || line.startsWith("[fallback]")
                          ? "text-amber-700 font-semibold"
                          : "text-slate-700"
                      }
                    >
                      {line}
                    </span>
                  </div>
                ))
              ) : (
                <div className="flex gap-3 py-1">
                  <span className="text-slate-400 w-12 select-none">[--]</span>
                  <span className="text-slate-500 italic">Waiting for logs...</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
