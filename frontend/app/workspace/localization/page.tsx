import { Suspense } from "react";

import LocalizationClient from "./LocalizationClient";

export default function LocalizationPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen bg-white text-slate-900 flex items-center justify-center font-sans">
          Loading localization workspace...
        </div>
      }
    >
      <LocalizationClient />
    </Suspense>
  );
}

