"use client";

import { useEffect, useRef, useState } from "react";
import { UploadCloud, Play, Terminal, CircleHelp } from "lucide-react";
import Link from "next/link";

import { ApiHttpError, createTask, getTask, getUploadUrl, TaskRecord } from "../../../lib/api";
import { SwapMode, resolvePresetInputKey } from "../../../lib/presets";
import { resolveAssetUrl } from "../../../lib/url";

const TERMINAL_STATUSES = new Set(["succeeded", "success", "success_degraded", "failed", "done"]);
const POLL_INITIAL_MS = 1000;
const POLL_MAX_MS = 15000;
const POLL_STILL_PROCESSING_MS = 60000;
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

type Props = {
  service?: "swap" | "action_replica" | "avatar";
};

function getActionReplicaContract(modeLike: string | null | undefined): {
  mode: "basic" | "intelligent";
  provider: "wan26_r2v" | "kling_motioncontrol_v3_pro";
} {
  const value = String(modeLike || "").trim().toLowerCase();
  if (value === "intelligent" || value === "intelligence") {
    return {
      mode: "intelligent",
      provider: "kling_motioncontrol_v3_pro",
    };
  }
  return {
    mode: "basic",
    provider: "wan26_r2v",
  };
}

function getSwapContract(
  modeLike: string | null | undefined,
  intensityLike: "balanced" | "strong_identity" | "extreme_replace",
): {
  mode: "basic" | "intelligent";
  provider: "akool_swap_face" | "swap_intelligence_akool";
  faceFidelity: "high" | "balanced" | "stable";
  replacementIntensity?: "balanced" | "strong_identity" | "extreme_replace";
} {
  const mode = ["intelligent", "intelligence"].includes(String(modeLike || "").trim().toLowerCase())
    ? "intelligent"
    : "basic";
  if (mode !== "intelligent") {
    return {
      mode: "basic",
      provider: "akool_swap_face",
      faceFidelity: intensityLike === "strong_identity" ? "high" : "balanced",
    };
  }
  const replacementIntensity =
    intensityLike === "extreme_replace" || intensityLike === "strong_identity"
      ? intensityLike
      : "balanced";
  return {
    mode: "intelligent",
    provider: "swap_intelligence_akool",
    faceFidelity: replacementIntensity === "balanced" ? "balanced" : "high",
    replacementIntensity,
  };
}

function normalizeSwapProxyProfile(value: string | null | undefined): "standard" | "tight" | "extreme_close" {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "tight") return "tight";
  if (normalized === "extreme_close" || normalized === "proxy_extreme_close" || normalized === "proxy_extreme") {
    return "extreme_close";
  }
  return "standard";
}

function getQualityGradeTone(value: string | null | undefined): string {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "success_clean") return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (normalized === "success_degraded") return "text-rose-700 bg-rose-50 border-rose-200";
  if (normalized === "failed") return "text-rose-700 bg-rose-50 border-rose-200";
  return "text-slate-600 bg-slate-50 border-slate-200";
}

function getMetricTone(value: number): string {
  if (value >= 0.75) return "text-emerald-700";
  if (value >= 0.55) return "text-amber-700";
  return "text-rose-700";
}

export default function SwapClient({ service = "swap" }: Props) {
  const serviceType = service;
  const isSwap = serviceType === "swap";
  const isAvatar = serviceType === "action_replica" || serviceType === "avatar";
  const intelligentModeValue: SwapMode = isAvatar ? "intelligent" : "intelligence";

  const [mode, setMode] = useState<SwapMode>("basic");
  const [swapSubtype, setSwapSubtype] = useState<"scene" | "face">("face");
  const [inputSource, setInputSource] = useState<"preset" | "upload">("upload");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [sourceFaceFiles, setSourceFaceFiles] = useState<File[]>([]);
  const [inputVideoUrl, setInputVideoUrl] = useState<string | null>(null);
  const [inputImageUrl, setInputImageUrl] = useState<string | null>(null);
  const [sourceFacePreviewUrls, setSourceFacePreviewUrls] = useState<string[]>([]);
  const [keepOriginalAudio, setKeepOriginalAudio] = useState(true);
  const [faceFidelity, setFaceFidelity] = useState<"balanced" | "strong_identity" | "extreme_replace">("balanced");
  const [proxyProfile, setProxyProfile] = useState<"standard" | "tight" | "extreme_close">("standard");
  const [faceEnhance, setFaceEnhance] = useState(true);
  const [orientation, setOrientation] = useState<"front" | "auto">("front");
  const [prompt, setPrompt] = useState<string>("");
  const [negativePrompt, setNegativePrompt] = useState<string>("");
  const [promptStrength, setPromptStrength] = useState<"low" | "medium" | "high">("medium");
  const [promptProfile, setPromptProfile] = useState<"balanced" | "camera_priority" | "motion_priority" | "identity_priority">("balanced");
  const [expressionMode, setExpressionMode] = useState<"natural" | "neutral" | "vivid">("natural");
  const [fidelityBias, setFidelityBias] = useState<"identity" | "balanced" | "motion">("balanced");
  const [preserveCamera, setPreserveCamera] = useState(true);
  const [preserveMotion, setPreserveMotion] = useState(true);
  const [preserveTiming, setPreserveTiming] = useState(true);
  const [preserveBackground, setPreserveBackground] = useState(true);
  const [actionReplicaAudioStrategy, setActionReplicaAudioStrategy] = useState<"keep_original" | "mute_original">("keep_original");
  const [orientationStrategy, setOrientationStrategy] = useState<"auto" | "prefer_video_motion" | "prefer_image_identity">("auto");
  const [showPromptTips, setShowPromptTips] = useState(false);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uiPollingWarning, setUiPollingWarning] = useState<string | null>(null);
  const [uiLogs, setUiLogs] = useState<string[]>([]);
  const [isPollingPaused, setIsPollingPaused] = useState(false);
  const [activeTab, setActiveTab] = useState<"playground" | "json" | "api">("playground");
  const [swapResultTab, setSwapResultTab] = useState<"preview" | "manifest">("preview");
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
      return;
    }
    setMode("basic");
    setSwapSubtype("face");
    setInputSource("upload");
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
    if (String(serviceType || "swap").toLowerCase() === "swap") {
      const intelligence = ["intelligent", "intelligence"].includes(String(mode || "basic").toLowerCase());
      if (intelligence) {
        if (!sourceFaceFiles.length && imageFile) {
          setSourceFaceFiles([imageFile]);
        }
        setFaceFidelity((current) => (current === "balanced" ? "extreme_replace" : current));
        setProxyProfile((current) => (current === "standard" ? "standard" : current));
        setFaceEnhance(faceFidelity === "extreme_replace" ? false : true);
      } else {
        if (!imageFile && sourceFaceFiles[0]) {
          setImageFile(sourceFaceFiles[0]);
        }
        setFaceFidelity("balanced");
        setProxyProfile("standard");
        setFaceEnhance(true);
      }
      return;
    }
    if (!isAvatar) return;
    const contract = getActionReplicaContract(mode);
    if (contract.mode === "intelligent") {
      setExpressionMode("neutral");
      setFidelityBias("motion");
      setOrientationStrategy("prefer_video_motion");
      setPromptProfile("motion_priority");
    } else {
      setExpressionMode("natural");
      setFidelityBias("balanced");
      setOrientationStrategy("auto");
      setPromptProfile("balanced");
    }
  }, [isAvatar, mode, serviceType, faceFidelity, imageFile, sourceFaceFiles]);

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

  useEffect(() => {
    if (!sourceFaceFiles.length) {
      setSourceFacePreviewUrls([]);
      return;
    }
    const previewUrls = sourceFaceFiles.map((file) => URL.createObjectURL(file));
    setSourceFacePreviewUrls(previewUrls);
    return () => {
      previewUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [sourceFaceFiles]);

  const serviceApi = String(serviceType || "swap").toLowerCase();
  const modeApi = String(mode || "basic").toLowerCase() as SwapMode;
  const isIntelligenceMode = modeApi === "intelligent" || modeApi === "intelligence";
  const actionReplicaContract = getActionReplicaContract(modeApi);
  const actionReplicaMode = actionReplicaContract.mode;
  const actionReplicaProvider = actionReplicaContract.provider;
  const swapContract = getSwapContract(modeApi, faceFidelity);
  const swapProvider = swapContract.provider;
  const swapSourcePackEnabled = isSwap && isIntelligenceMode;
  const effectiveProxyProfile = isIntelligenceMode ? normalizeSwapProxyProfile(proxyProfile) : "standard";
  const activeSwapSourceFaceFiles = swapSourcePackEnabled ? sourceFaceFiles : (imageFile ? [imageFile] : []);
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

  const buildSwapTaskPayload = (
    sourceVideoKey: string,
    sourceFaceImageKeys: string[],
    emitDebug = false,
    options?: { forceProxyOverride?: boolean },
  ) => {
    const sourceFaceImageKey = sourceFaceImageKeys[0];
    const payload = {
      service_type: "swap" as const,
      mode: swapContract.mode,
      swap_type: "face" as const,
      source_video_key: sourceVideoKey,
      source_face_image_key: sourceFaceImageKey,
      source_face_images: swapSourcePackEnabled ? sourceFaceImageKeys : undefined,
      keep_original_audio: keepOriginalAudio,
      face_fidelity: swapContract.faceFidelity,
      replacement_intensity: swapContract.replacementIntensity,
      proxy_profile: swapSourcePackEnabled ? effectiveProxyProfile : undefined,
      face_enhance: faceEnhance,
      force_proxy_override: Boolean(options?.forceProxyOverride),
      inputs: {
        source_video_key: sourceVideoKey,
        source_video_url: sourceVideoKey,
        source_face_image_key: sourceFaceImageKey,
        source_face_image: sourceFaceImageKey,
        source_face_images: swapSourcePackEnabled ? sourceFaceImageKeys : undefined,
        proxy_profile: swapSourcePackEnabled ? effectiveProxyProfile : undefined,
        force_proxy_override: Boolean(options?.forceProxyOverride),
      },
    };
    if (emitDebug) {
      console.info("[swap][submit][client]", {
        source_face_count: sourceFaceImageKeys.length,
        has_source_face_image: Boolean(sourceFaceImageKey),
        has_source_face_images: Boolean(sourceFaceImageKeys.length),
        has_source_video: Boolean(payload.inputs.source_video_key),
        has_source_video_url: Boolean(payload.inputs.source_video_url),
        proxy_profile: payload.inputs.proxy_profile,
        force_proxy_override: payload.inputs.force_proxy_override,
      });
    }
    return payload;
  };

  const getPollDelayMs = (attempt: number) => {
    return Math.min(POLL_MAX_MS, POLL_INITIAL_MS * Math.pow(2, attempt));
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
                  `Temporary gateway issue, retrying (fallback to CDN) (HTTP ${apiStatus}).`,
                );
              }
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
        if (
          elapsed > POLL_STILL_PROCESSING_MS &&
          !shouldStopPolling(latest) &&
          String(latest.stage || "").toLowerCase() === "running"
        ) {
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
              `Temporary gateway issue, retrying (fallback to CDN) (HTTP ${status}).`,
            );
          }
          scheduleNext();
          return;
        }

        if (status !== undefined) {
          setError(err instanceof Error ? err.message : "Polling failed unexpectedly.");
          setIsPollingPaused(true);
          setIsRunning(false);
          if (pollRef.current) {
            window.clearTimeout(pollRef.current);
            pollRef.current = null;
          }
          return;
        }

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

  const handleRetryWithProxyProbe = async () => {
    if (!isSwap || isRunning) return;
    try {
      setError(null);
      setUiPollingWarning(null);
      setIsPollingPaused(false);
      setUiLogs([]);
      setIsRunning(true);
      setTask(null);
      cancelPolling();
      let sourceVideoKey = "";
      let sourceFaceImageKeys: string[] = [];
      const snapshot = taskMetadata.run_config_snapshot && typeof taskMetadata.run_config_snapshot === "object"
        ? (taskMetadata.run_config_snapshot as Record<string, unknown>)
        : {};
      if (videoFile) {
        sourceVideoKey = await uploadFileToR2(videoFile);
      } else {
        sourceVideoKey = String(snapshot.source_video_key || snapshot.source_video_url || "");
      }
      if (activeSwapSourceFaceFiles.length) {
        for (const file of activeSwapSourceFaceFiles) {
          sourceFaceImageKeys.push(await uploadFileToR2(file));
        }
      } else if (Array.isArray(snapshot.source_face_images) && snapshot.source_face_images.length) {
        sourceFaceImageKeys = (snapshot.source_face_images as unknown[]).map((value) => String(value)).filter(Boolean);
      } else if (snapshot.source_face_image_key) {
        sourceFaceImageKeys = [String(snapshot.source_face_image_key)];
      }
      if (!sourceVideoKey || !sourceFaceImageKeys.length) {
        throw new Error("Retry with proxy probe requires source video and source face inputs.");
      }
      const result = await createTask(buildSwapTaskPayload(sourceVideoKey, sourceFaceImageKeys, true, { forceProxyOverride: true }));
      startTaskPolling(result.task_id);
    } catch (err) {
      setIsRunning(false);
      setError(err instanceof Error ? err.message : "Retry with proxy probe failed.");
    }
  };

  const runAvatarTask = async (overrides?: { motionKey?: string; characterKey?: string; modeOverride?: SwapMode }) => {
    const characterKey = overrides?.characterKey || (await uploadFileToR2(imageFile as File));
    const motionKey = overrides?.motionKey || (await uploadFileToR2(videoFile as File));
    const contract = getActionReplicaContract(overrides?.modeOverride || modeApi);
    return createTask({
      service_type: "action_replica",
      model_id: "kling-v2.6-std-motion",
      mode: contract.mode,
      input_key: motionKey,
      inputs: {
        provider: contract.provider,
        character_image_url: characterKey,
        source_video_url: motionKey,
        // legacy aliases kept for backward compatibility
        character_image: characterKey,
        motion_video: motionKey,
        character_orientation: orientation,
        preserve_camera: preserveCamera,
        preserve_motion: preserveMotion,
        preserve_timing: preserveTiming,
        preserve_background: preserveBackground,
        audio_strategy: actionReplicaAudioStrategy,
        orientation_strategy: orientationStrategy,
        prompt_source: prompt.trim() ? "user" : "default",
        user_prompt: prompt.trim() ? prompt.trim() : undefined,
        prompt_profile: promptProfile,
        prompt: prompt.trim() ? prompt.trim() : undefined,
        negative_prompt: negativePrompt.trim() ? negativePrompt.trim() : undefined,
        prompt_strength: promptStrength,
        expression_mode: expressionMode,
        fidelity_bias: fidelityBias,
        candidate_count: 1,
        seed_strategy: "fixed",
      }
    });
  };

  const handleRun = async () => {
    cancelPolling();
    if (isSwap && (!videoFile || (!swapSourcePackEnabled && !imageFile) || (swapSourcePackEnabled && sourceFaceFiles.length === 0))) {
      setError("Please upload a source face image and source video for swap.");
      return;
    }
    if (isAvatar && (!imageFile || !videoFile)) {
      setError("Please upload a character image and source video for action replica mode.");
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
      } else if (isSwap) {
        const sourceVideoKey = await uploadFileToR2(videoFile as File);
        const sourceFaceImageKeys: string[] = [];
        for (const file of activeSwapSourceFaceFiles) {
          sourceFaceImageKeys.push(await uploadFileToR2(file));
        }
        result = await createTask(buildSwapTaskPayload(sourceVideoKey, sourceFaceImageKeys, true));
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

  const taskOutputs = task?.outputs && typeof task.outputs === "object"
    ? (task.outputs as Record<string, unknown>)
    : {};
  const metadataOutputs = task?.metadata?.outputs && typeof task.metadata.outputs === "object"
    ? (task.metadata.outputs as Record<string, unknown>)
    : {};
  const manifestPreviewOutputs = task?.metadata?.manifest_preview
    && typeof task.metadata.manifest_preview === "object"
    && (task.metadata.manifest_preview as Record<string, unknown>).outputs
    && typeof (task.metadata.manifest_preview as Record<string, unknown>).outputs === "object"
      ? ((task.metadata.manifest_preview as Record<string, unknown>).outputs as Record<string, unknown>)
      : {};
  const outputUrl =
    resolveAssetUrl(
      String(
        taskOutputs.video_url ||
        taskOutputs.result_url ||
        metadataOutputs.video_url ||
        metadataOutputs.result_url ||
        manifestPreviewOutputs.video_url ||
        manifestPreviewOutputs.result_url ||
        task?.output_url ||
        "",
      ) || null,
    ) ??
    (task?.output_key && cdnBase ? `${cdnBase}/${task.output_key}` : null);
  const manifestUrl = resolveAssetUrl(
    String(
      taskOutputs.manifest_url ||
      metadataOutputs.manifest_url ||
      manifestPreviewOutputs.manifest_url ||
      "",
    ) || null,
  );
  const manifestPreview = task?.metadata?.manifest_preview
    ? JSON.stringify(task.metadata.manifest_preview, null, 2)
    : "";
  // Preview priority: output -> local upload (upload mode) -> empty placeholder.
  const previewUrl = outputUrl ?? (inputSource === "upload" ? inputVideoUrl : null);
  const logs = [...uiLogs, ...(task?.logs ?? [])];
  const latestRemoteStatus = (() => {
    for (let i = logs.length - 1; i >= 0; i -= 1) {
      const line = logs[i] || "";
      const m = line.match(/\[ar\]\[poll\].*remote_status=([a-z_]+)/i);
      if (m?.[1]) return m[1].toLowerCase();
    }
    return "";
  })();
  const taskId = task?.task_id ?? task?.id ?? "";
  const showUploadBlocks = (isSwap && (swapSubtype === "face" || inputSource === "upload")) || isAvatar;
  const lowerError = (task?.error || "").toLowerCase();
  const hasPolicyViolation =
    lowerError.includes("content_policy_violation") ||
    logs.some((line) => line.toLowerCase().includes("content_policy_violation"));
  const showPolicyPanel = isAvatar && modeApi === "intelligent" && hasPolicyViolation;
  const failedReason =
    (task?.status || "").toLowerCase() === "failed"
      ? task?.error || logs[logs.length - 1] || "Task failed."
      : null;
  const hasPollTimeoutFailure = Boolean(
    failedReason && failedReason.toLowerCase().includes("poll timeout"),
  );
  const taskMetadata = task?.metadata && typeof task.metadata === "object"
    ? (task.metadata as Record<string, unknown>)
    : {};
  const finalDecision = taskMetadata.final_decision && typeof taskMetadata.final_decision === "object"
    ? (taskMetadata.final_decision as Record<string, unknown>)
    : {};
  const resultAnalysis = taskMetadata.result_analysis && typeof taskMetadata.result_analysis === "object"
    ? (taskMetadata.result_analysis as Record<string, unknown>)
    : taskMetadata.quality_analysis && typeof taskMetadata.quality_analysis === "object"
      ? (taskMetadata.quality_analysis as Record<string, unknown>)
      : {};
  const taskRequestId = String(taskMetadata.request_id || "");
  const taskProviderStatus = String(finalDecision.provider_status ?? "");
  const taskBusinessStatus = String(finalDecision.business_status ?? "");
  const taskDeliveryStatus = String(finalDecision.delivery_status ?? "");
  const taskDeliveryAllowed = String(finalDecision.delivery_status === "allowed");
  const taskRequiresManualReview = String(finalDecision.requires_manual_review ?? "");
  const taskRunnerOutcome = String(finalDecision.runner_outcome ?? taskMetadata.runner_outcome ?? "");
  const taskModeSummary = String(taskMetadata.mode || (task?.mode || "") || "");
  const taskProviderSummary = String(taskMetadata.provider || (isSwap ? swapProvider : ""));
  const taskEngineSummary = String(taskMetadata.engine || "");
  const taskModelIdSummary = String(taskMetadata.model_id || "");
  const taskPromptSource = String(taskMetadata.prompt_source || "");
  const taskPromptProfile = String(taskMetadata.prompt_profile || "");
  const taskPromptStrength = String(taskMetadata.prompt_strength || "");
  const taskExpressionMode = String(taskMetadata.expression_mode || "");
  const taskFidelityBias = String(taskMetadata.fidelity_bias || "");
  const taskAudioStrategy = String(taskMetadata.audio_strategy || "");
  const taskOrientationStrategy = String(taskMetadata.orientation_strategy || "");
  const taskResolvedOrientation = String(taskMetadata.resolved_character_orientation || "");
  const taskPriorityPolicy = String(taskMetadata.priority_policy || "");
  const taskCandidateCount = String(taskMetadata.candidate_count || "");
  const taskRemoteStatus = String(taskMetadata.remote_status || latestRemoteStatus || "");
  const taskElapsedMs = String(taskMetadata.elapsed_ms || "");
  const taskDetectStage = String(taskMetadata.detect_stage || "");
  const taskSubmitStage = String(taskMetadata.submit_stage || "");
  const taskKeepOriginalAudio = String(taskMetadata.keep_original_audio ?? "");
  const taskFaceEnhance = String(taskMetadata.face_enhance ?? "");
  const taskSwapStrength = String(taskMetadata.swap_strength || "");
  const taskRouteIntent = String(taskMetadata.route_intent || "");
  const taskRouteExecutionStyle = String(taskMetadata.route_execution_style || "");
  const taskRouteSummary = String((taskMetadata.quality_summary as { route_summary?: string } | undefined)?.route_summary || taskMetadata.route_summary || "");
  const taskSingleFaceOnly = String(taskMetadata.single_face_only ?? "");
  const taskFaceCountLimit = String(taskMetadata.face_count_limit ?? "");
  const taskSourceFaceScore = String(taskMetadata.source_face_score ?? "");
  const taskReplacementIntensity = String(taskMetadata.replacement_intensity ?? taskMetadata.swap_strength ?? "");
  const taskRequestedProxyProfile = String(finalDecision.requested_proxy_profile ?? "");
  const taskEffectiveProxyProfile = String(finalDecision.effective_proxy_profile ?? "");
  const finalModifyVideoSource = String(finalDecision.modify_video_source_final ?? "");
  const finalDegradeReason = String(finalDecision.degrade_reason_final ?? "");
  const finalSubmissionMode = String(finalDecision.submission_mode_final ?? "");
  const finalExtremeRequested = String(finalDecision.extreme_requested ?? "");
  const finalExtremeExecuted = String(finalDecision.extreme_executed ?? "");
  const finalExtremeEffective = String(finalDecision.extreme_effective ?? "");
  const finalGatePrimaryResult = String(finalDecision.extreme_gate_primary_result ?? "");
  const finalGateResult = String(finalDecision.extreme_gate_final_result ?? "");
  const finalOverrideApplied = String(finalDecision.override_applied ?? "");
  const finalQualityGrade = String(finalDecision.quality_grade ?? "");
  const finalResultGrade = String(finalDecision.result_grade ?? "");
  const finalResultBucket = String(finalDecision.result_bucket ?? "");
  const proxyThresholdRequired = String(taskMetadata.proxy_face_ratio_threshold_required ?? "");
  const proxyFaceRatioActual = String(taskMetadata.proxy_face_ratio_after_actual ?? "");
  const proxyRejectedReason = String(finalDecision.proxy_rejected_reason ?? "");
  const primaryGateReason = String(finalDecision.primary_gate_reason ?? "");
  const proxyChannelGateReason = String(finalDecision.proxy_channel_gate_reason ?? "");
  const rawChannelGateReason = String(finalDecision.raw_channel_gate_reason ?? "");
  const reviewQueueCandidate = String(finalDecision.review_queue_candidate ?? taskMetadata.review_queue_candidate ?? "");
  const rerunRecommended = String(finalDecision.rerun_recommended ?? "");
  const rerunStrategy = String(finalDecision.rerun_strategy ?? "");
  const manualMaterialFixRequired = String(finalDecision.manual_material_fix_required ?? "");
  const routeChannelRequested = String(finalDecision.route_channel_requested ?? "");
  const routeChannelEffective = String(finalDecision.route_channel_effective ?? "");
  const channelSwitchReason = String(finalDecision.channel_switch_reason ?? "");
  const sourceImageTags = Array.isArray(taskMetadata.source_image_tags) ? (taskMetadata.source_image_tags as unknown[]).map((value) => String(value)).filter(Boolean) : [];
  const targetVideoTags = Array.isArray(taskMetadata.target_video_tags) ? (taskMetadata.target_video_tags as unknown[]).map((value) => String(value)).filter(Boolean) : [];
  const taskStatusNormalized = String(task?.status || "").trim().toLowerCase();
  const isDegradedStatus = taskStatusNormalized === "success_degraded";
  const resultFacePresenceRatio = Number(resultAnalysis.face_presence_ratio ?? 0);
  const resultFaceStabilityScore = Number(resultAnalysis.face_stability_score ?? 0);
  const resultOverwriteConfidence = Number(resultAnalysis.identity_overwrite_confidence ?? 0);
  const resultTrackQualityConfidence = Number(resultAnalysis.track_quality_confidence ?? 0);
  const resultProxyExecutionConfidence = Number(resultAnalysis.proxy_execution_confidence ?? 0);
  const taskSourcePackSize = String(taskMetadata.source_pack_size ?? "");
  const taskSelectedSourceFaceIndex = String(taskMetadata.selected_source_face_index ?? "");
  const taskSelectedSourceFaceReason = String(taskMetadata.source_selection_reason ?? "");
  const taskTargetTrackFaceScore = String(taskMetadata.target_track_face_score ?? "");
  const taskTargetMappingFaceScore = String(taskMetadata.target_mapping_face_score ?? taskMetadata.target_face_score ?? "");
  const taskSelectedTargetFrameIndex = String(taskMetadata.selected_target_frame_index ?? "");
  const taskReplacementMode = String(taskMetadata.replacement_mode ?? "");
  const taskDegradedFallbackUsed = String(taskMetadata.degraded_fallback_used ?? "");
  const taskFaceEnhanceUsed = String(taskMetadata.face_enhance_used ?? taskMetadata.face_enhance ?? "");
  const taskTargetRankReason = String(taskMetadata.target_rank_reason ?? taskMetadata.target_mapping_face_rank_reason ?? "");
  const taskRiskTags = Array.isArray(taskMetadata.risk_tags)
    ? (taskMetadata.risk_tags as unknown[]).map((value) => String(value)).filter(Boolean)
    : [];
  const canUseSafeDemo = Boolean(safeDemoMotionKey && safeDemoCharacterKey) && !isRunning;
  const canRun = isAvatar
    ? Boolean(videoFile && imageFile) && !isRunning
    : Boolean(videoFile && (swapSourcePackEnabled ? sourceFaceFiles.length > 0 : imageFile)) && !isRunning;
  const previewSourceFaceKeys = swapSourcePackEnabled
    ? ["(source face key 1)", "(source face key 2)", "(source face key 3)"]
    : ["(source face image key)"];
  const swapPayloadPreview = buildSwapTaskPayload("(source video key)", previewSourceFaceKeys);
  const payloadPreview = isAvatar
    ? {
        service_type: "action_replica",
        model_id: "kling-v2.6-std-motion",
        mode: actionReplicaMode,
        input_key: "(motion key)",
        inputs: {
          provider: actionReplicaProvider,
          character_image_url: "(character key)",
          source_video_url: "(motion key)",
          character_image: "(character key)",
          motion_video: "(motion key)",
          character_orientation: orientation,
          preserve_camera: preserveCamera,
          preserve_motion: preserveMotion,
          preserve_timing: preserveTiming,
          preserve_background: preserveBackground,
          audio_strategy: actionReplicaAudioStrategy,
          orientation_strategy: orientationStrategy,
          prompt_source: prompt ? "user" : "default",
          user_prompt: prompt ? "(optional)" : "",
          prompt_profile: promptProfile,
          prompt: prompt ? "(optional)" : "",
          negative_prompt: negativePrompt ? "(optional)" : "",
          prompt_strength: promptStrength,
          expression_mode: expressionMode,
          fidelity_bias: fidelityBias,
          candidate_count: 1,
          seed_strategy: "fixed",
        }
      }
    : {
        ...swapPayloadPreview,
        single_face_only: true,
        face_count_limit: 1,
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
        `  -d '{\"service_type\":\"action_replica\",\"model_id\":\"kling-v2.6-std-motion\",\"mode\":\"${actionReplicaMode}\",\"input_key\":\"<source_key>\",\"inputs\":{\"provider\":\"${actionReplicaProvider}\",\"character_image_url\":\"<character_key>\",\"source_video_url\":\"<source_key>\",\"preserve_camera\":${preserveCamera},\"preserve_motion\":${preserveMotion},\"preserve_timing\":${preserveTiming},\"preserve_background\":${preserveBackground},\"audio_strategy\":\"${actionReplicaAudioStrategy}\",\"orientation_strategy\":\"${orientationStrategy}\",\"character_orientation\":\"${orientation}\",\"prompt_source\":\"${prompt ? "user" : "default"}\",\"user_prompt\":\"<optional>\",\"prompt_profile\":\"${promptProfile}\",\"prompt\":\"<optional>\",\"negative_prompt\":\"<optional>\",\"prompt_strength\":\"${promptStrength}\",\"expression_mode\":\"${expressionMode}\",\"fidelity_bias\":\"${fidelityBias}\",\"candidate_count\":1,\"seed_strategy\":\"fixed\"}}'`
      ].join(" \\\n")
    : [
        `curl -X POST \"${apiBase}/api/v1/tasks\"`,
        "  -H \"Content-Type: application/json\"",
        `  -d '${JSON.stringify(swapPayloadPreview)}'`
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
            onClick={() => setMode(intelligentModeValue)}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              (mode === "intelligent" || mode === "intelligence")
                ? "bg-white text-blue-600 shadow-sm border border-slate-200 ring-1 ring-black/5"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Intelligence
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
                {isSwap ? (
                  <div className="space-y-3">
                    <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                      Swap Type
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setSwapSubtype("face")}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                          swapSubtype === "face"
                            ? "border-blue-600 text-blue-600 bg-blue-50"
                            : "border-slate-200 text-slate-500 bg-white"
                        }`}
                      >
                        Face
                      </button>
                      <button
                        type="button"
                        disabled
                        className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-200 text-slate-400 bg-slate-50 cursor-not-allowed"
                      >
                        Scene Coming Soon
                      </button>
                    </div>
                    <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
                      Single-face only for v1.x. Basic is the production baseline. Intelligence stays the enhanced comparison tier.
                    </div>
                  </div>
                ) : null}
                {showUploadBlocks ? (
                  <>
                    {isAvatar || (isSwap && swapSubtype === "face") ? (
                      <>
                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            {isAvatar ? "Character Image" : "Source Face Image"}
                          </label>
                          {swapSourcePackEnabled ? (
                            <>
                              <div className="flex items-center justify-between text-[11px] text-slate-400">
                                <span>{sourceFaceFiles.length ? `${sourceFaceFiles.length} source faces selected` : "No files selected"}</span>
                                {sourceFaceFiles.length ? (
                                  <button
                                    type="button"
                                    className="text-slate-500 hover:text-slate-700"
                                    onClick={() => setSourceFaceFiles([])}
                                  >
                                    Clear
                                  </button>
                                ) : null}
                              </div>
                              <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-[11px] text-blue-800">
                                Recommend 3–5 images: frontal, left angle, right angle, neutral, smile.
                              </div>
                            </>
                          ) : (
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
                          )}
                          <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                            <div className="relative flex flex-col items-center justify-center">
                              <input
                                type="file"
                                accept="image/*"
                                multiple={swapSourcePackEnabled}
                                onChange={(event) => {
                                  const files = Array.from(event.target.files || []).slice(0, 5);
                                  if (swapSourcePackEnabled) {
                                    setSourceFaceFiles(files);
                                  } else {
                                    setImageFile(files[0] || null);
                                  }
                                }}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                              <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                                <UploadCloud className="w-5 h-5 text-slate-600" />
                              </div>
                              <span className="text-xs font-medium text-slate-600">
                                {swapSourcePackEnabled ? "Click to upload source pack" : "Click to upload image"}
                              </span>
                              {swapSourcePackEnabled ? (
                                <span className="text-[10px] text-slate-400 mt-1">drag and drop or multi-select up to 5 images</span>
                              ) : null}
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2">
                              {swapSourcePackEnabled ? (
                                sourceFacePreviewUrls.length ? (
                                  <div className="space-y-2">
                                    {sourceFacePreviewUrls.map((previewUrl, index) => (
                                      <div key={`${previewUrl}-${index}`} className="flex items-center gap-3">
                                        <img
                                          src={previewUrl}
                                          alt={`Source face ${index + 1}`}
                                          className="h-10 w-10 rounded-md object-cover"
                                        />
                                        <div className="min-w-0 flex-1">
                                          <div className="truncate text-[11px] text-slate-500">{sourceFaceFiles[index]?.name || `Source face ${index + 1}`}</div>
                                          <div className="text-[10px] text-slate-400">Ref {index + 1}</div>
                                        </div>
                                        <div className="flex items-center gap-1">
                                          <button
                                            type="button"
                                            className="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-50 disabled:opacity-40"
                                            disabled={index === 0}
                                            onClick={() =>
                                              setSourceFaceFiles((current) => {
                                                const next = [...current];
                                                [next[index - 1], next[index]] = [next[index], next[index - 1]];
                                                return next;
                                              })
                                            }
                                          >
                                            ↑
                                          </button>
                                          <button
                                            type="button"
                                            className="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-50 disabled:opacity-40"
                                            disabled={index === sourceFaceFiles.length - 1}
                                            onClick={() =>
                                              setSourceFaceFiles((current) => {
                                                const next = [...current];
                                                [next[index + 1], next[index]] = [next[index], next[index + 1]];
                                                return next;
                                              })
                                            }
                                          >
                                            ↓
                                          </button>
                                          <button
                                            type="button"
                                            className="text-[10px] text-slate-500 hover:text-slate-700"
                                            onClick={() =>
                                              setSourceFaceFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))
                                            }
                                          >
                                            Remove
                                          </button>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div className="flex items-center justify-center text-[11px] text-slate-400">
                                    Source pack previews will appear here
                                  </div>
                                )
                              ) : inputImageUrl ? (
                                <div className="flex items-center gap-3">
                                  <img
                                    src={inputImageUrl}
                                    alt="Character preview"
                                    className="h-10 w-10 rounded-md object-cover"
                                  />
                                  <span className="text-[11px] text-slate-500">
                                    {isAvatar ? "Character preview ready" : "Source face preview ready"}
                                  </span>
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
                            Source Video
                            <span className="text-[10px] font-normal text-slate-400">
                              {isAvatar ? "MP4, 4-8s" : "MP4, 5-10s"}
                            </span>
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
                            Provider
                          </label>
                          <select
                            value={actionReplicaProvider}
                            disabled
                            className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="wan26_r2v">WAN 2.6 (Baseline)</option>
                            <option value="kling_motioncontrol_v3_pro">Kling Motion Control V3 Pro (Intelligent)</option>
                          </select>
                          <p className="text-[11px] text-slate-500">
                            {actionReplicaMode === "intelligent"
                              ? "Intelligent mode is fixed to Kling Motion Control V3 Pro."
                              : "Baseline mode is fixed to WAN 2.6."}
                          </p>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Preserve Strategy
                          </label>
                          <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                            <label className="flex items-center justify-between text-xs text-slate-600">
                              <span>Preserve Camera</span>
                              <input
                                type="checkbox"
                                checked={preserveCamera}
                                onChange={(event) => setPreserveCamera(event.target.checked)}
                              />
                            </label>
                            <label className="flex items-center justify-between text-xs text-slate-600">
                              <span>Preserve Motion</span>
                              <input
                                type="checkbox"
                                checked={preserveMotion}
                                onChange={(event) => setPreserveMotion(event.target.checked)}
                              />
                            </label>
                            <label className="flex items-center justify-between text-xs text-slate-600">
                              <span>Preserve Timing</span>
                              <input
                                type="checkbox"
                                checked={preserveTiming}
                                onChange={(event) => setPreserveTiming(event.target.checked)}
                              />
                            </label>
                            <label className="flex items-center justify-between text-xs text-slate-600">
                              <span>Preserve Background</span>
                              <input
                                type="checkbox"
                                checked={preserveBackground}
                                onChange={(event) => setPreserveBackground(event.target.checked)}
                              />
                            </label>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Orientation Strategy
                          </label>
                              <select
                            value={orientationStrategy}
                            onChange={(event) =>
                              setOrientationStrategy(
                                event.target.value as "auto" | "prefer_video_motion" | "prefer_image_identity",
                              )
                            }
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="auto">Auto</option>
                            <option value="prefer_video_motion">Prefer Video Motion</option>
                            <option value="prefer_image_identity">Prefer Image Identity</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Orientation (Optional)
                          </label>
                          <select
                            value={orientation}
                            onChange={(event) =>
                              setOrientation(event.target.value as "front" | "auto")
                            }
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="front">Front</option>
                            <option value="auto">Auto</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Prompt Profile
                          </label>
                          <select
                            value={promptProfile}
                            onChange={(event) =>
                              setPromptProfile(
                                event.target.value as "balanced" | "camera_priority" | "motion_priority" | "identity_priority",
                              )
                            }
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="balanced">Balanced</option>
                            <option value="motion_priority">Motion Priority</option>
                            <option value="camera_priority">Camera Priority</option>
                            <option value="identity_priority">Identity Priority</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Audio Strategy
                          </label>
                          <select
                            value={actionReplicaAudioStrategy}
                            onChange={(event) => setActionReplicaAudioStrategy(event.target.value as "keep_original" | "mute_original")}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="keep_original">Keep Original</option>
                            <option value="mute_original">Mute Original</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Prompt Strength
                          </label>
                          <select
                            value={promptStrength}
                            onChange={(event) =>
                              setPromptStrength(event.target.value as "low" | "medium" | "high")
                            }
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="low">Low</option>
                            <option value="medium">Medium</option>
                            <option value="high">High</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Expression Mode
                          </label>
                          <select
                            value={expressionMode}
                            onChange={(event) => setExpressionMode(event.target.value as "natural" | "neutral" | "vivid")}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="natural">Natural</option>
                            <option value="neutral">Neutral</option>
                            <option value="vivid">Vivid</option>
                          </select>
                        </div>

                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Fidelity Bias
                          </label>
                          <select
                            value={fidelityBias}
                            onChange={(event) => setFidelityBias(event.target.value as "identity" | "balanced" | "motion")}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                          >
                            <option value="balanced">Balanced</option>
                            <option value="motion">Motion</option>
                            <option value="identity">Identity</option>
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
                            placeholder="Optional prompt to emphasize motion/style preservation, background continuity, or subject behavior"
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400"
                          />
                          <input
                            type="text"
                            value={negativePrompt}
                            onChange={(event) => setNegativePrompt(event.target.value)}
                            placeholder="Optional negative prompt to avoid scene drift, identity drift, background redesign, etc."
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400"
                          />
                        </div>
                      </>
                    ) : null}

                    {isSwap && swapSubtype === "face" ? (
                      <div className="pt-6 border-t border-slate-100">
                        <div className="space-y-3">
                          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                            Provider
                          </label>
                          <div className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                            {swapProvider}
                          </div>
                        </div>
                        {isIntelligenceMode ? (
                          <div className="space-y-3 mt-4">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                              Replacement Intensity
                            </label>
                            <select
                              value={faceFidelity}
                              onChange={(event) => {
                                const value = event.target.value as "balanced" | "strong_identity" | "extreme_replace";
                                setFaceFidelity(value);
                                if (value === "extreme_replace") {
                                  setFaceEnhance(false);
                                }
                              }}
                              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                            >
                              <option value="balanced">balanced</option>
                              <option value="strong_identity">strong_identity</option>
                              <option value="extreme_replace">extreme_replace</option>
                            </select>
                          </div>
                        ) : null}
                        {isIntelligenceMode ? (
                          <div className="space-y-3 mt-4">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                              Proxy Profile
                            </label>
                            <select
                              value={proxyProfile}
                              onChange={(event) => {
                                setProxyProfile(normalizeSwapProxyProfile(event.target.value));
                              }}
                              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                            >
                              <option value="standard">Standard</option>
                              <option value="tight">Tight</option>
                              <option value="extreme_close">Extreme Close</option>
                            </select>
                          </div>
                        ) : (
                          <div className="space-y-3 mt-4">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                              Face Fidelity
                            </label>
                            <select
                              value={faceFidelity}
                              onChange={(event) => setFaceFidelity(event.target.value as "balanced" | "strong_identity")}
                              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                            >
                              <option value="balanced">balanced</option>
                              <option value="strong_identity">high</option>
                            </select>
                          </div>
                        )}
                        <div className="flex justify-between items-center py-2 mt-4">
                          <span className="text-sm font-medium text-slate-700">Face Enhance</span>
                          <button
                            type="button"
                            onClick={() => setFaceEnhance((prev) => !prev)}
                            className={`w-10 h-6 rounded-full relative cursor-pointer shadow-inner ${
                              faceEnhance ? "bg-blue-600" : "bg-slate-300"
                            }`}
                          >
                            <div
                              className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow-sm transition-all ${
                                faceEnhance ? "right-1" : "left-1"
                              }`}
                            ></div>
                          </button>
                        </div>
                        <div className="flex justify-between items-center py-2 mt-4">
                          <span className="text-sm font-medium text-slate-700">Keep Original Audio</span>
                          <button
                            type="button"
                            onClick={() => setKeepOriginalAudio((prev) => !prev)}
                            className={`w-10 h-6 rounded-full relative cursor-pointer shadow-inner ${
                              keepOriginalAudio ? "bg-blue-600" : "bg-slate-300"
                            }`}
                          >
                            <div
                              className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow-sm transition-all ${
                                keepOriginalAudio ? "right-1" : "left-1"
                              }`}
                            ></div>
                          </button>
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                          {isIntelligenceMode ? (
                            <>
                              Intelligence uses the enhanced single-face comparison route.
                              <br />
                              Single-face input only.
                            </>
                          ) : (
                            <>
                              Basic uses the stable single-face baseline route.
                              <br />
                              Single-face input only.
                            </>
                          )}
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : null}
                {error ? <p className="text-xs text-rose-500">{error}</p> : null}
                {failedReason ? <p className="text-xs text-rose-500">Failure reason: {failedReason}</p> : null}
                {hasPollTimeoutFailure ? (
                  <p className="text-xs text-amber-700">
                    This task may still be running. Refresh later or check Fal dashboard with Request ID.
                  </p>
                ) : null}
                {hasPolicyViolation ? (
                  <p className="text-xs text-amber-700">
                    Safety checker blocked this request. Try a safer reference video or prompt.
                  </p>
                ) : null}
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
                {isSwap && finalQualityGrade === "success_degraded" ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 space-y-2">
                    <div className="font-semibold">Degraded output</div>
                    <div>This run is marked degraded and is not delivery-ready by default.</div>
                    <button
                      type="button"
                      onClick={handleRetryWithProxyProbe}
                      disabled={isRunning}
                      className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-[11px] font-semibold text-blue-700 disabled:opacity-60"
                    >
                      Retry with Proxy Probe
                    </button>
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
                    {taskRequestId ? (
                      <div className="flex items-center gap-2">
                        <span>Request ID: {taskRequestId}</span>
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(taskRequestId)}
                          className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-600"
                        >
                          Copy
                        </button>
                      </div>
                    ) : null}
                    <div>
                      Status: {task?.status || "queued"} · Stage: {task?.stage || "queued"}
                    </div>
                    {isAvatar ? (
                      <div className="mt-1 space-y-0.5 text-[11px] text-slate-600">
                        <div>Mode: {taskModeSummary || "-"}</div>
                        <div>Provider: {taskProviderSummary || "-"}</div>
                        <div>Engine: {taskEngineSummary || "-"}</div>
                        <div>Model ID: {taskModelIdSummary || "-"}</div>
                        <div>Prompt Source: {taskPromptSource || "-"}</div>
                        <div>Prompt Profile: {taskPromptProfile || "-"}</div>
                        <div>Prompt Strength: {taskPromptStrength || "-"}</div>
                        <div>Audio Strategy: {taskAudioStrategy || "-"}</div>
                        <div>Expression Mode: {taskExpressionMode || "-"}</div>
                        <div>Fidelity Bias: {taskFidelityBias || "-"}</div>
                        <div>Orientation Strategy: {taskOrientationStrategy || "-"}</div>
                        <div>Resolved Orientation: {taskResolvedOrientation || "-"}</div>
                        <div>Priority Policy: {taskPriorityPolicy || "-"}</div>
                        <div>Candidate Count: {taskCandidateCount || "1"}</div>
                        <div>Remote Status: {latestRemoteStatus || "-"}</div>
                      </div>
                    ) : null}
                    {isSwap ? (
                      <div className="mt-1 space-y-0.5 text-[11px] text-slate-600">
                        <div>Mode: {taskModeSummary || task?.mode || "-"}</div>
                        <div>Provider: {taskProviderSummary || "-"}</div>
                        <div>Swap Strength: {taskSwapStrength || (isIntelligenceMode ? "strong_identity" : "balanced")}</div>
                        <div>Replacement Intensity: {taskReplacementIntensity || "-"}</div>
                        <div>Route Intent: {taskRouteIntent || "-"}</div>
                        <div>Execution Style: {taskRouteExecutionStyle || "-"}</div>
                        <div>Route Summary: {taskRouteSummary || "-"}</div>
                        <div>Quality Grade: <span className={`inline-flex rounded border px-2 py-0.5 ${getQualityGradeTone(finalQualityGrade)}`}>{finalQualityGrade || "-"}</span></div>
                        <div>Final Submission Mode: {finalSubmissionMode || "-"}</div>
                        <div>Outcome: {taskRunnerOutcome || "-"}</div>
                        <div>Provider Status: {taskProviderStatus || "-"}</div>
                        <div>Business Status: {taskBusinessStatus || "-"}</div>
                        <div>Delivery Status: {taskDeliveryStatus || "-"}</div>
                        <div>Delivery Allowed: {taskDeliveryAllowed || "-"}</div>
                        <div>Requires Manual Review: {taskRequiresManualReview || "-"}</div>
                        <div>Review Queue Candidate: {reviewQueueCandidate || "-"}</div>
                        <div>Single-Face Only: {taskSingleFaceOnly ? taskSingleFaceOnly : "true"}</div>
                        <div>Face Count Limit: {taskFaceCountLimit || "1"}</div>
                        <div>Request ID: {taskRequestId || "-"}</div>
                        <div>Face Enhance: {taskFaceEnhance ? taskFaceEnhance : "-"}</div>
                        <div>Face Enhance Used: {taskFaceEnhanceUsed || "-"}</div>
                        <div>Keep Original Audio: {taskKeepOriginalAudio ? taskKeepOriginalAudio : "-"}</div>
                        <div>Detect Stage: {taskDetectStage || "-"}</div>
                        <div>Submit Stage: {taskSubmitStage || "-"}</div>
                        <div>Source Face Score: {taskSourceFaceScore || "-"}</div>
                        <div>Source Pack Size: {taskSourcePackSize || "-"}</div>
                        <div>Selected Source Ref: {taskSelectedSourceFaceIndex || "-"}</div>
                        <div>Selected Source Ref Reason: {taskSelectedSourceFaceReason || "-"}</div>
                        <div>Target Mapping Face Score: {taskTargetMappingFaceScore || "-"}</div>
                        <div>Target Track Face Score: {taskTargetTrackFaceScore || "-"}</div>
                        <div>Target Rank Reason: {taskTargetRankReason || "-"}</div>
                        <div>Selected Anchor Frame: {taskSelectedTargetFrameIndex || "-"}</div>
                        <div>Requested Proxy Profile: {taskRequestedProxyProfile || "-"}</div>
                        <div>Effective Proxy Profile: {taskEffectiveProxyProfile || "-"}</div>
                        <div>Final ModifyVideo Source: {finalModifyVideoSource || "-"}</div>
                        <div>Primary Gate Result: {finalGatePrimaryResult || "-"}</div>
                        <div>Final Gate Result: {finalGateResult || "-"}</div>
                        <div>Primary Gate Reason: {primaryGateReason || "-"}</div>
                        <div>Proxy Channel Gate Reason: {proxyChannelGateReason || "-"}</div>
                        <div>Raw Channel Gate Reason: {rawChannelGateReason || "-"}</div>
                        <div>Override Applied: {finalOverrideApplied || "false"}</div>
                        <div>Replacement Mode: {taskReplacementMode || "-"}</div>
                        <div>Degrade Reason: {finalDegradeReason || "-"}</div>
                        <div>Degraded Fallback Used: {taskDegradedFallbackUsed || "false"}</div>
                        <div>Extreme Requested: {finalExtremeRequested || "false"}</div>
                        <div>Extreme Executed: {finalExtremeExecuted || "false"}</div>
                        <div>Extreme Effective: {finalExtremeEffective || "false"}</div>
                        <div>Risk Tags: {taskRiskTags.length ? taskRiskTags.join(", ") : "-"}</div>
                        <div>Remote Status: {taskRemoteStatus || "-"}</div>
                        <div>Elapsed: {taskElapsedMs ? `${taskElapsedMs} ms` : "-"}</div>
                      </div>
                    ) : null}
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
                isIntelligenceMode
                  ? "bg-blue-600 hover:bg-blue-700 shadow-blue-200"
                  : "bg-slate-800 hover:bg-slate-900 shadow-slate-200"
              } ${!canRun ? "opacity-60 cursor-not-allowed" : ""}`}
            >
              <Play className="w-4 h-4" />
              {isRunning ? "Running..." : `Run ${isIntelligenceMode ? "Intelligence" : "Basic"}`}
            </button>
            <div className="text-center mt-3 text-[10px] text-slate-400 font-medium">
              Estimated Cost: {isIntelligenceMode ? "$0.15" : "$0.05"}
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
            {isSwap && (outputUrl || manifestUrl) ? (
              <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm text-sm text-slate-700">
                <div className="mb-3 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setSwapResultTab("preview")}
                    className={`rounded-md px-3 py-1.5 text-xs font-semibold ${swapResultTab === "preview" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600"}`}
                  >
                    Result
                  </button>
                  <button
                    type="button"
                    onClick={() => setSwapResultTab("manifest")}
                    className={`rounded-md px-3 py-1.5 text-xs font-semibold ${swapResultTab === "manifest" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600"}`}
                  >
                    Manifest
                  </button>
                </div>
                {swapResultTab === "preview" ? (
                  <div className="space-y-2">
                    {outputUrl ? (
                      <div>
                        Video: <a href={outputUrl} target="_blank" rel="noreferrer" className={`underline ${taskDeliveryStatus === "blocked" ? "text-amber-700" : "text-blue-600"}`}>{taskDeliveryStatus === "blocked" ? "Open review-only result.mp4" : "Open result.mp4"}</a>
                      </div>
                    ) : null}
                    {taskDeliveryStatus === "blocked" ? (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        This result is not recommended for delivery.
                      </div>
                    ) : null}
                    <div className="grid gap-4 text-xs text-slate-600 sm:grid-cols-2">
                      <div className="space-y-1 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Requested</div>
                        <div>Replacement Intensity: {taskReplacementIntensity || "-"}</div>
                        <div>Proxy Profile: {taskRequestedProxyProfile || "-"}</div>
                        <div>Face Enhance: {taskFaceEnhance || "-"}</div>
                      </div>
                      <div className="space-y-1 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Effective</div>
                        <div>Effective Proxy Profile: {taskEffectiveProxyProfile || "-"}</div>
                        <div>Extreme Effective: {finalExtremeEffective || "false"}</div>
                        <div>Final ModifyVideo Source: {finalModifyVideoSource || "-"}</div>
                        <div>Degrade Reason Final: {finalDegradeReason || "-"}</div>
                        <div>Quality Grade: <span className={`inline-flex rounded border px-2 py-0.5 ${getQualityGradeTone(finalQualityGrade)}`}>{finalQualityGrade || "-"}</span></div>
                        <div>Result Grade: {finalResultGrade || "-"}</div>
                        <div>Result Bucket: {finalResultBucket || "-"}</div>
                        <div>Delivery Status: {taskDeliveryStatus || "-"}</div>
                        <div>Requires Manual Review: {taskRequiresManualReview || "-"}</div>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
                      <div>Mode: {taskModeSummary || task?.mode || "-"}</div>
                      <div>Swap Strength: {taskSwapStrength || "-"}</div>
                      <div>Route Summary: {taskRouteSummary || "-"}</div>
                      <div>Replacement Mode: {taskReplacementMode || "-"}</div>
                      <div>Source Face Score: {taskSourceFaceScore || "-"}</div>
                      <div>Target Mapping Face Score: {taskTargetMappingFaceScore || "-"}</div>
                      <div>Target Track Face Score: {taskTargetTrackFaceScore || "-"}</div>
                      <div>Selected Anchor Frame: {taskSelectedTargetFrameIndex || "-"}</div>
                      <div>Primary Gate Result: {finalGatePrimaryResult || "-"}</div>
                      <div>Final Gate Result: {finalGateResult || "-"}</div>
                      <div>Primary Gate Reason: {primaryGateReason || "-"}</div>
                      <div>Proxy Channel Gate Reason: {proxyChannelGateReason || "-"}</div>
                      <div>Raw Channel Gate Reason: {rawChannelGateReason || "-"}</div>
                      <div>Override Applied: {finalOverrideApplied || "false"}</div>
                      <div>Proxy Threshold Required: {proxyThresholdRequired || "-"}</div>
                      <div>Proxy Face Ratio Actual: {proxyFaceRatioActual || "-"}</div>
                      <div>Proxy Used Final: {String(finalDecision.proxy_used_final ?? "") || "-"}</div>
                      <div>Proxy Rejected Reason: {proxyRejectedReason || "-"}</div>
                      <div>Rerun Recommended: {rerunRecommended || "false"}</div>
                      <div>Rerun Strategy: {rerunStrategy || "-"}</div>
                      <div>Manual Material Fix Required: {manualMaterialFixRequired || "false"}</div>
                      <div>Outcome: {taskRunnerOutcome || "-"}</div>
                      <div>Review Queue Candidate: {reviewQueueCandidate || "-"}</div>
                      <div>Route Channel Requested: {routeChannelRequested || "-"}</div>
                      <div>Route Channel Effective: {routeChannelEffective || "-"}</div>
                      <div>Channel Switch Reason: {channelSwitchReason || "-"}</div>
                      <div>Source Image Tags: {sourceImageTags.length ? sourceImageTags.join(", ") : "-"}</div>
                      <div>Target Video Tags: {targetVideoTags.length ? targetVideoTags.join(", ") : "-"}</div>
                      <div>Risk Tags: {taskRiskTags.length ? taskRiskTags.join(", ") : "-"}</div>
                    </div>
                    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <div className="mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">Result Analysis</div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div className={getMetricTone(resultFacePresenceRatio)}>Face Presence Ratio: {resultFacePresenceRatio.toFixed(4)}</div>
                        <div className={getMetricTone(resultFaceStabilityScore)}>Face Stability Score: {resultFaceStabilityScore.toFixed(4)}</div>
                        <div className={getMetricTone(resultOverwriteConfidence)}>Identity Overwrite Confidence: {resultOverwriteConfidence.toFixed(4)}</div>
                        <div className={getMetricTone(resultTrackQualityConfidence)}>Track Quality Confidence: {resultTrackQualityConfidence.toFixed(4)}</div>
                        <div className={getMetricTone(resultProxyExecutionConfidence)}>Proxy Execution Confidence: {resultProxyExecutionConfidence.toFixed(4)}</div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {manifestUrl ? (
                      <div>
                        Manifest: <a href={manifestUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">Open manifest.json</a>
                      </div>
                    ) : null}
                    {manifestPreview ? (
                      <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-slate-50 p-3 text-xs">{manifestPreview}</pre>
                    ) : null}
                  </div>
                )}
              </div>
            ) : null}
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



