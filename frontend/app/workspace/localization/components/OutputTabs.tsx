"use client";

import { useEffect, useState } from "react";

type Outputs = {
  subtitle_url?: string;
  audio_url?: string;
  manifest_url?: string;
};

type Props = {
  outputUrl?: string | null;
  outputs?: Outputs;
};

type TabId = "video" | "subtitles" | "audio" | "manifest";

export default function OutputTabs({ outputUrl, outputs }: Props) {
  const [activeTab, setActiveTab] = useState<TabId>("video");
  const [subtitlePreview, setSubtitlePreview] = useState("");
  const [manifestPreview, setManifestPreview] = useState("");

  useEffect(() => {
    const subtitleUrl = outputs?.subtitle_url;
    if (!subtitleUrl) {
      setSubtitlePreview("");
      return;
    }
    fetch(subtitleUrl, { cache: "no-store" })
      .then((res) => (res.ok ? res.text() : ""))
      .then((text) => {
        const lines = text.split("\n").slice(0, 200).join("\n");
        setSubtitlePreview(lines);
      })
      .catch(() => setSubtitlePreview(""));
  }, [outputs?.subtitle_url]);

  useEffect(() => {
    const manifestUrl = outputs?.manifest_url;
    if (!manifestUrl) {
      setManifestPreview("");
      return;
    }
    fetch(manifestUrl, { cache: "no-store" })
      .then((res) => (res.ok ? res.text() : ""))
      .then((text) => setManifestPreview(text.slice(0, 6000)))
      .catch(() => setManifestPreview(""));
  }, [outputs?.manifest_url]);

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

      <div className="p-4 min-h-64">
        {activeTab === "video" ? (
          outputUrl ? (
            <video controls className="w-full rounded-lg border border-slate-200" src={outputUrl} />
          ) : (
            <div className="text-slate-400">No video output yet.</div>
          )
        ) : null}

        {activeTab === "subtitles" ? (
          <div className="space-y-3">
            {outputs?.subtitle_url ? (
              <a href={outputs.subtitle_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                Download
              </a>
            ) : (
              <div className="text-slate-400">Subtitle not ready.</div>
            )}
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap">
              {subtitlePreview || "No preview"}
            </pre>
          </div>
        ) : null}

        {activeTab === "audio" ? (
          <div className="space-y-3">
            {outputs?.audio_url ? (
              <>
                <audio controls className="w-full" src={outputs.audio_url} />
                <a href={outputs.audio_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                  Download
                </a>
              </>
            ) : (
              <div className="text-slate-400">Dub audio not ready.</div>
            )}
          </div>
        ) : null}

        {activeTab === "manifest" ? (
          <div className="space-y-3">
            {outputs?.manifest_url ? (
              <a href={outputs.manifest_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                Download
              </a>
            ) : (
              <div className="text-slate-400">Manifest not ready.</div>
            )}
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap">
              {manifestPreview || "No preview"}
            </pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}

