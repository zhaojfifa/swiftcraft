"use client";

import { useSearchParams } from "next/navigation";

import AvatarClient from "./avatar/AvatarClient";
import FollowVideoClient from "./follow-video/FollowVideoClient";
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

  if (service === "follow_video") {
    return <FollowVideoClient />;
  }

  return <SwapClient />;
}
