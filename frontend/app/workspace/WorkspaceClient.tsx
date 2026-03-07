"use client";

import { useSearchParams } from "next/navigation";

import AvatarClient from "./avatar/AvatarClient";
import LocalizationClient from "./localization/LocalizationClient";
import SwapClient from "./swap/SwapClient";

export default function WorkspaceClient() {
  const searchParams = useSearchParams();
  const service = (searchParams.get("service") || "swap").toLowerCase();

  if (service === "localization") {
    return <LocalizationClient />;
  }

  if (service === "action_replica" || service === "avatar") {
    return <AvatarClient />;
  }

  return <SwapClient />;
}
