"use client";

type Mode = "baseline" | "intelligent";

type Props = {
  mode: Mode;
  stage: string;
  status: string;
};

const BASELINE_STAGES = [
  "ANALYZING",
  "EXTRACTING",
  "TRANSCRIBING",
  "TRANSLATING",
  "SYNTHESIZING",
  "RENDERING",
  "UPLOADING",
  "DONE",
];

const INTELLIGENT_STAGES = [
  "ANALYZING",
  "EXTRACTING",
  "TRANSCRIBING",
  "TRANSLATING",
  "SYNTHESIZING",
  "ALIGNING",
  "LIP_SYNC",
  "RENDERING",
  "UPLOADING",
  "DONE",
];

export default function StagePanel({ mode, stage, status }: Props) {
  const stageList = mode === "intelligent" ? INTELLIGENT_STAGES : BASELINE_STAGES;
  const normalizedStage = String(stage || "").toUpperCase();
  const normalizedStatus = String(status || "").toLowerCase();

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Stage</h3>
        <span className="text-xs text-slate-500">{normalizedStage || "QUEUED"}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {stageList.map((item) => {
          const isActive = item === normalizedStage;
          const isDone = normalizedStatus === "done" || normalizedStatus === "succeeded";
          return (
            <div
              key={item}
              className={`rounded-lg border px-2 py-1.5 text-xs font-medium ${
                isActive
                  ? "border-blue-300 bg-blue-50 text-blue-700"
                  : isDone
                    ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                    : "border-slate-200 bg-slate-50 text-slate-500"
              }`}
            >
              {item}
            </div>
          );
        })}
      </div>
    </div>
  );
}
