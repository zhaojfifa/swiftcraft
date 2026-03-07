"use client";

import { Copy, UploadCloud } from "lucide-react";

type TopTab = "playground" | "json" | "api";
type AudioStrategy = "mute_original" | "keep_bgm" | "duck_original";

type Props = {
  mode: "baseline" | "intelligent";
  activeTopTab: TopTab;
  setActiveTopTab: (tab: TopTab) => void;
  videoFile: File | null;
  setVideoFile: (file: File | null) => void;
  inputVideoUrl: string | null;
  targetLang: string;
  setTargetLang: (value: string) => void;
  voiceId: string;
  setVoiceId: (value: string) => void;
  subtitleMode: "sidecar" | "burned";
  setSubtitleMode: (mode: "sidecar" | "burned") => void;
  audioStrategy: AudioStrategy;
  setAudioStrategy: (value: AudioStrategy) => void;
  dubGain: number;
  setDubGain: (value: number) => void;
  bgmGain: number;
  setBgmGain: (value: number) => void;
  voiceSpeed: number;
  setVoiceSpeed: (value: number) => void;
  lipsyncEnabled: boolean;
  setLipsyncEnabled: (value: boolean) => void;
  lipsyncScope: "face" | "full";
  setLipsyncScope: (value: "face" | "full") => void;
  isRunning: boolean;
  payloadPreview: unknown;
  curlSnippet: string;
  onRun: () => void;
};

export default function InputPanel({
  mode,
  activeTopTab,
  setActiveTopTab,
  videoFile,
  setVideoFile,
  inputVideoUrl,
  targetLang,
  setTargetLang,
  voiceId,
  setVoiceId,
  subtitleMode,
  setSubtitleMode,
  audioStrategy,
  setAudioStrategy,
  dubGain,
  setDubGain,
  bgmGain,
  setBgmGain,
  voiceSpeed,
  setVoiceSpeed,
  lipsyncEnabled,
  setLipsyncEnabled,
  lipsyncScope,
  setLipsyncScope,
  isRunning,
  payloadPreview,
  curlSnippet,
  onRun,
}: Props) {
  const copyText = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="w-[420px] bg-white border-r border-slate-200 flex flex-col z-10 shadow-[4px_0_24px_rgba(0,0,0,0.02)]">
      <div className="flex border-b border-slate-100 px-6 pt-6 gap-6 text-sm">
        <button
          className={`pb-3 transition ${
            activeTopTab === "playground"
              ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
              : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setActiveTopTab("playground")}
        >
          Playground
        </button>
        <button
          className={`pb-3 transition ${
            activeTopTab === "json"
              ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
              : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setActiveTopTab("json")}
        >
          JSON
        </button>
        <button
          className={`pb-3 transition ${
            activeTopTab === "api"
              ? "text-slate-900 border-b-2 border-slate-900 font-semibold"
              : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setActiveTopTab("api")}
        >
          API
        </button>
      </div>

      <div className="p-6 space-y-6 overflow-y-auto flex-1">
        {activeTopTab === "json" ? (
          <div className="space-y-3">
            <pre className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap break-all">
              {JSON.stringify(payloadPreview, null, 2)}
            </pre>
            <button
              type="button"
              onClick={() => copyText(JSON.stringify(payloadPreview, null, 2))}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
            >
              <Copy className="w-3.5 h-3.5" />
              Copy JSON
            </button>
          </div>
        ) : null}

        {activeTopTab === "api" ? (
          <div className="space-y-3">
            <pre className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap break-all">
              {curlSnippet}
            </pre>
            <button
              type="button"
              onClick={() => copyText(curlSnippet)}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
            >
              <Copy className="w-3.5 h-3.5" />
              Copy curl
            </button>
          </div>
        ) : null}

        {activeTopTab === "playground" ? (
          <div className="space-y-6">
            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                Source Video
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
              <div className="group relative grid h-44 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
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
                </div>
                <div className="rounded-lg border border-slate-200 bg-white p-2">
                  {inputVideoUrl ? (
                    <div className="flex items-center gap-3">
                      <video src={inputVideoUrl} muted playsInline className="h-10 w-14 rounded-md object-cover" />
                      <span className="text-[11px] text-slate-500">Video preview ready</span>
                    </div>
                  ) : (
                    <div className="flex items-center justify-center text-[11px] text-slate-400">Preview will appear here</div>
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Target Language</label>
              <select
                value={targetLang}
                onChange={(event) => setTargetLang(event.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
              >
                <option value="my">Burmese (my)</option>
                <option value="en">English (en)</option>
              </select>
            </div>

            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Voice</label>
              <select
                value={voiceId}
                onChange={(event) => setVoiceId(event.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
              >
                <option value="mm_female_1">mm_female_1</option>
                <option value="mm_male_1">mm_male_1</option>
              </select>
            </div>

            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Subtitle Output</label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setSubtitleMode("sidecar")}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                    subtitleMode === "sidecar"
                      ? "border-blue-600 text-blue-600 bg-blue-50"
                      : "border-slate-200 text-slate-500 bg-white"
                  }`}
                >
                  Sidecar
                </button>
                <button
                  type="button"
                  onClick={() => setSubtitleMode("burned")}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                    subtitleMode === "burned"
                      ? "border-blue-600 text-blue-600 bg-blue-50"
                      : "border-slate-200 text-slate-500 bg-white"
                  }`}
                >
                  Burned
                </button>
              </div>
            </div>

            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Audio Controls</label>
              <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-500">Audio Strategy</label>
                <select
                  value={audioStrategy}
                  onChange={(event) => setAudioStrategy(event.target.value as AudioStrategy)}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
                >
                  <option value="mute_original">Mute Original (Default)</option>
                  <option value="keep_bgm">Keep BGM (Experimental)</option>
                  <option value="duck_original">Duck Original (Experimental)</option>
                </select>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-slate-500">Dub Volume</label>
                  <span className="text-xs text-slate-500">{dubGain.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min={0.6}
                  max={1.6}
                  step={0.05}
                  value={dubGain}
                  onChange={(event) => setDubGain(Number(event.target.value) || 1.0)}
                  className="w-full accent-blue-600"
                />
              </div>
              {audioStrategy !== "mute_original" ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-semibold text-slate-500">BGM Volume</label>
                    <span className="text-xs text-slate-500">{bgmGain.toFixed(2)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={0.6}
                    step={0.05}
                    value={bgmGain}
                    onChange={(event) => setBgmGain(Number(event.target.value) || 0.0)}
                    className="w-full accent-blue-600"
                  />
                </div>
              ) : null}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-slate-500">Playback / Voice Speed</label>
                  <span className="text-xs text-slate-500">{voiceSpeed.toFixed(2)}x</span>
                </div>
                <input
                  type="range"
                  min={0.85}
                  max={1.2}
                  step={0.05}
                  value={voiceSpeed}
                  onChange={(event) => setVoiceSpeed(Number(event.target.value) || 1.0)}
                  className="w-full accent-blue-600"
                />
              </div>
            </div>

            {mode === "intelligent" ? (
              <div className="space-y-3 rounded-xl border border-blue-200 bg-blue-50 p-3">
                <label className="flex items-center gap-2 text-sm font-semibold text-blue-900">
                  <input
                    type="checkbox"
                    checked={lipsyncEnabled}
                    onChange={(event) => setLipsyncEnabled(event.target.checked)}
                  />
                  Enable Lip Sync
                </label>
                {lipsyncEnabled ? (
                  <>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setLipsyncScope("face")}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                          lipsyncScope === "face"
                            ? "border-blue-600 text-blue-600 bg-white"
                            : "border-blue-200 text-blue-700 bg-blue-100"
                        }`}
                      >
                        Face
                      </button>
                      <button
                        type="button"
                        onClick={() => setLipsyncScope("full")}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${
                          lipsyncScope === "full"
                            ? "border-blue-600 text-blue-600 bg-white"
                            : "border-blue-200 text-blue-700 bg-blue-100"
                        }`}
                      >
                        Full
                      </button>
                    </div>
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                      Lip sync may increase runtime and can fail on low-quality faces.
                    </div>
                  </>
                ) : (
                  <div className="text-xs text-blue-800">Lip sync is off by default.</div>
                )}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                Baseline mode forces lip sync off.
              </div>
            )}
          </div>
        ) : null}
      </div>

      <div className="p-6 border-t border-slate-100 bg-white">
        <button
          type="button"
          onClick={onRun}
          disabled={!videoFile || isRunning}
          className="w-full py-3.5 rounded-xl font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60"
        >
          {isRunning ? "Running..." : "Preview"}
        </button>
      </div>
    </div>
  );
}
