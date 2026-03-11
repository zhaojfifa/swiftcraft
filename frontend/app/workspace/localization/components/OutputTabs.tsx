"use client";

import { useEffect, useState } from "react";

type TabId = "video" | "subtitles" | "audio" | "manifest";

type Props = {
  activeTab: TabId;
  setActiveTab: (tab: TabId) => void;
  videoUrl: string | null;
  subtitleUrl: string | null;
  subtitleAssUrl: string | null;
  audioUrl: string | null;
  manifestUrl: string | null;
  manifestFallback: unknown;
};

export default function OutputTabs({
  activeTab,
  setActiveTab,
  videoUrl,
  subtitleUrl,
  subtitleAssUrl,
  audioUrl,
  manifestUrl,
  manifestFallback,
}: Props) {
  const [subtitlePreview, setSubtitlePreview] = useState("");
  const [manifestPreview, setManifestPreview] = useState("");

  useEffect(() => {
    if (!subtitleUrl) {
      setSubtitlePreview("");
      return;
    }
    fetch(subtitleUrl, { cache: "no-store" })
      .then((res) => (res.ok ? res.text() : ""))
      .then((text) => setSubtitlePreview(text.split("\n").slice(0, 200).join("\n")))
      .catch(() => setSubtitlePreview(""));
  }, [subtitleUrl]);

  useEffect(() => {
    if (!manifestUrl) {
      setManifestPreview("");
      return;
    }
    fetch(manifestUrl, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) return "";
        const raw = await res.text();
        if (!raw.trim()) return "";
        try {
          return JSON.stringify(JSON.parse(raw), null, 2).slice(0, 10000);
        } catch {
          return raw.slice(0, 10000);
        }
      })
      .then((text) => setManifestPreview(text))
      .catch(() => setManifestPreview(""));
  }, [manifestUrl]);

  const manifestText = manifestPreview || JSON.stringify(manifestFallback || {}, null, 2);
  const manifestObj = (() => {
    try {
      return manifestText ? (JSON.parse(manifestText) as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();
  const subtitleBurned = String(manifestObj?.subtitle_burned ?? "n/a");
  const sourceSubtitleType = String(manifestObj?.source_subtitle_type ?? "n/a");
  const audioStrategy = String(manifestObj?.audio_strategy ?? "n/a");
  const originalAudioMuted = String(manifestObj?.original_audio_muted ?? "n/a");
  const originalSubtitleRemoved = String(manifestObj?.original_subtitle_removed ?? "n/a");
  const originalSubtitleSuppressed = String(manifestObj?.original_subtitle_suppressed ?? "n/a");
  const subtitleProcessing = (manifestObj?.subtitle_processing as Record<string, unknown> | undefined) || {};
  const cleanupEnabled = String(subtitleProcessing.cleanup_enabled ?? "n/a");
  const cleanupStrategy = String(subtitleProcessing.cleanup_strategy ?? "n/a");
  const outputDuration = String(
    (manifestObj?.metrics as Record<string, unknown> | undefined)?.total_latency_ms ?? "n/a",
  );
  const translationRatio = String(
    (manifestObj?.translation as Record<string, unknown> | undefined)?.length_ratio_avg ?? "n/a",
  );
  const warningCount = Array.isArray(
    (manifestObj?.tts as Record<string, unknown> | undefined)?.segment_alignment,
  )
    ? (
        (manifestObj?.tts as Record<string, unknown>).segment_alignment as Array<Record<string, unknown>>
      ).reduce((acc, row) => {
        const flags = row.warning_flags;
        return acc + (Array.isArray(flags) ? flags.length : 0);
      }, 0)
    : 0;
  const audioQa = (manifestObj?.audio_qa as Record<string, unknown> | undefined) || manifestObj || {};
  const dubRms = String((audioQa as Record<string, unknown>)?.dub_rms_db ?? "n/a");
  const mixedRms = String((audioQa as Record<string, unknown>)?.mixed_rms_db ?? "n/a");
  const localizedRms = String((audioQa as Record<string, unknown>)?.localized_rms_db ?? "n/a");
  const localizedPeak = String((audioQa as Record<string, unknown>)?.localized_peak_db ?? "n/a");

  return (
    <div className="w-full rounded-xl border border-slate-200 bg-white overflow-hidden">
      <div className="flex border-b border-slate-200">
        {[
          ["video", "Video"],
          ["subtitles", "Subtitles"],
          ["audio", "Dub Audio"],
          ["manifest", "Manifest"],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id as TabId)}
            className={`px-4 py-2 text-sm ${activeTab === id ? "text-blue-600 border-b-2 border-blue-600" : "text-slate-500"}`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="p-4 min-h-72">
        {activeTab === "video" ? (
          videoUrl ? (
            <video controls className="w-full rounded-lg border border-slate-200" src={videoUrl} />
          ) : (
            <div className="text-slate-400">No video output yet.</div>
          )
        ) : null}

        {activeTab === "subtitles" ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-4 text-sm">
              {subtitleUrl ? (
                <a href={subtitleUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                  Download SRT
                </a>
              ) : null}
              {subtitleAssUrl ? (
                <a href={subtitleAssUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                  Download ASS
                </a>
              ) : null}
              {!subtitleUrl && !subtitleAssUrl ? <div className="text-slate-400">Not available.</div> : null}
            </div>
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap">
              {subtitlePreview || "No preview (or blocked by CORS)."}
            </pre>
          </div>
        ) : null}

        {activeTab === "audio" ? (
          <div className="space-y-3">
            {audioUrl ? (
              <>
                <audio controls className="w-full" src={audioUrl} />
                <a href={audioUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                  Download audio
                </a>
              </>
            ) : (
              <div className="text-slate-400">Not available.</div>
            )}
          </div>
        ) : null}

        {activeTab === "manifest" ? (
          <div className="space-y-3">
            {manifestUrl ? (
              <a href={manifestUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                Open manifest URL
              </a>
            ) : (
              <div className="text-slate-400">Not available via URL, showing metadata preview.</div>
            )}
            <div className="grid grid-cols-2 gap-2 rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
              <div>subtitle burned: {subtitleBurned}</div>
              <div>source subtitle type: {sourceSubtitleType}</div>
              <div>audio strategy: {audioStrategy}</div>
              <div>original audio muted: {originalAudioMuted}</div>
              <div>subtitle removed: {originalSubtitleRemoved}</div>
              <div>subtitle suppressed: {originalSubtitleSuppressed}</div>
              <div>cleanup enabled: {cleanupEnabled}</div>
              <div>cleanup strategy: {cleanupStrategy}</div>
              <div>output duration(ms): {outputDuration}</div>
              <div>translation ratio: {translationRatio}</div>
              <div>warnings: {warningCount}</div>
              <div>dub rms(db): {dubRms}</div>
              <div>mixed rms(db): {mixedRms}</div>
              <div>localized rms(db): {localizedRms}</div>
              <div>localized peak(db): {localizedPeak}</div>
            </div>
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap break-all">
              {manifestText}
            </pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}
