"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

type Props = {
  status: string;
  stage: string;
  logs: string[];
};

export default function LogsPanel({ status, stage, logs }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="w-full rounded-xl border border-slate-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3 text-left border-b border-slate-200"
      >
        <div>
          <div className="text-sm font-semibold text-slate-900">Logs</div>
          <div className="text-xs text-slate-500">Status: {status || "queued"} | Stage: {stage || "SUBMITTED"}</div>
        </div>
        <ChevronDown className={`w-4 h-4 text-slate-500 transition-transform ${collapsed ? "-rotate-90" : "rotate-0"}`} />
      </button>
      {!collapsed ? (
        <div className="p-3 h-44 overflow-y-auto font-mono text-xs">
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
      ) : null}
    </div>
  );
}
