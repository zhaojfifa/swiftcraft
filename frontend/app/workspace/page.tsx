import { Suspense } from "react";

import WorkspaceClient from "./WorkspaceClient";

export default function WorkspacePage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen bg-white text-slate-900 flex items-center justify-center font-sans">
          Loading workspace...
        </div>
      }
    >
      <WorkspaceClient />
    </Suspense>
  );
}
