"use client";

import { useEffect, useState } from "react";

type TabId = "video" | "subtitles" | "audio" | "manifest";

type Props = {
  activeTab: TabId;
  setActiveTab: (tab: TabId) => void;
  videoUrl: string | null;
  subtitleUrl: string | null;
  audioUrl: string | null;
  manifestUrl: string | null;
  manifestFallback: unknown;
};

export default function OutputTabs({
  activeTab,
  setActiveTab,
  videoUrl,
  subtitleUrl,
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
            {subtitleUrl ? (
              <a href={subtitleUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                Download subtitle
              </a>
            ) : (
              <div className="text-slate-400">Not available.</div>
            )}
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
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap break-all">
              {manifestText}
            </pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}
