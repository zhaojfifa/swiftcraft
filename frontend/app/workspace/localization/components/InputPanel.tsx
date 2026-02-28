"use client";

import { UploadCloud } from "lucide-react";

type Props = {
  mode: "baseline" | "intelligent" | "enhanced";
  setMode: (mode: "baseline" | "intelligent" | "enhanced") => void;
  videoFile: File | null;
  setVideoFile: (file: File | null) => void;
  inputVideoUrl: string | null;
  preserveBgm: boolean;
  setPreserveBgm: (v: boolean) => void;
  ducking: boolean;
  setDucking: (v: boolean) => void;
  isRunning: boolean;
  onRun: () => void;
};

export default function InputPanel({
  mode,
  setMode,
  videoFile,
  setVideoFile,
  inputVideoUrl,
  preserveBgm,
  setPreserveBgm,
  ducking,
  setDucking,
  isRunning,
  onRun,
}: Props) {
  return (
    <div className="w-[380px] bg-white border-r border-slate-200 flex flex-col">
      <div className="p-5 space-y-4 overflow-y-auto">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setMode("baseline")}
            className={`px-3 py-1.5 rounded border text-sm ${mode === "baseline" ? "bg-slate-900 text-white border-slate-900" : "bg-white text-slate-600 border-slate-300"}`}
          >
            Baseline
          </button>
          <button
            type="button"
            disabled
            className="px-3 py-1.5 rounded border text-sm bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed"
            title="LipSync compare in progress"
          >
            Intelligent (Coming soon)
          </button>
          <button
            type="button"
            disabled
            className="px-3 py-1.5 rounded border text-sm bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed"
          >
            Enhanced (Coming soon)
          </button>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
            Source Video (4-60s)
          </label>
          <div className="group relative grid h-44 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3">
            <div className="relative flex flex-col items-center justify-center">
              <input
                type="file"
                accept="video/*"
                onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
              <div className="p-3 bg-white rounded-full shadow-sm mb-3 border border-slate-100">
                <UploadCloud className="w-5 h-5 text-slate-600" />
              </div>
              <span className="text-xs font-medium text-slate-600">Click to upload video</span>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-2">
              {inputVideoUrl ? (
                <div className="flex items-center gap-3">
                  <video src={inputVideoUrl} muted playsInline className="h-10 w-14 rounded-md object-cover" />
                  <span className="text-[11px] text-slate-500">{videoFile?.name || "Video ready"}</span>
                </div>
              ) : (
                <div className="flex items-center justify-center text-[11px] text-slate-400">Preview will appear here</div>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Target Language</label>
          <select disabled className="w-full rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-sm text-slate-700">
            <option value="my">Burmese (my)</option>
          </select>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Voice Mode</label>
          <select disabled className="w-full rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-sm text-slate-700">
            <option value="mm_female_1">Standard TTS (mm_female_1)</option>
          </select>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Subtitle Output</label>
          <div className="flex gap-2">
            <button type="button" className="px-3 py-1.5 rounded border text-sm bg-slate-900 text-white border-slate-900">
              Sidecar
            </button>
            <button type="button" disabled className="px-3 py-1.5 rounded border text-sm bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed">
              Burn-in
            </button>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Audio Controls</label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={preserveBgm} onChange={(e) => setPreserveBgm(e.target.checked)} />
            Preserve BGM
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={ducking} onChange={(e) => setDucking(e.target.checked)} />
            Ducking
          </label>
        </div>
      </div>
      <div className="p-5 border-t border-slate-200">
        <button
          type="button"
          onClick={onRun}
          disabled={!videoFile || isRunning || mode !== "baseline"}
          className="w-full py-3 rounded-lg font-semibold text-white bg-blue-600 disabled:opacity-60"
        >
          {isRunning ? "Running..." : "Run Localization"}
        </button>
      </div>
    </div>
  );
}

