"use client";

type Props = {
  status: string;
  stage: string;
  logs: string[];
};

export default function LogsPanel({ status, stage, logs }: Props) {
  return (
    <div className="w-full mt-4">
      <div className="text-xs text-slate-500 mb-1">Status: {status || "queued"} · Stage: {stage || "SUBMITTED"}</div>
      <div className="bg-white border border-slate-200 rounded-lg p-3 h-44 overflow-y-auto font-mono text-xs">
        {logs.length ? (
          logs.map((line, idx) => (
            <div key={`${idx}-${line}`} className="py-1 border-b border-slate-50 text-slate-700">
              [{String(idx + 1).padStart(2, "0")}] {line}
            </div>
          ))
        ) : (
          <div className="text-slate-400 italic">Waiting for logs...</div>
        )}
      </div>
    </div>
  );
}

