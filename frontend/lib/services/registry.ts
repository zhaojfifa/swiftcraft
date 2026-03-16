export type ServiceId = "swap" | "action_replica" | "localization" | "follow_video";
export type InputFieldType = "video" | "image" | "select" | "text";

export type ServiceInput = {
  id: string;
  type: InputFieldType;
  label: string;
};

export type ServiceConfig = {
  id: ServiceId;
  title: string;
  description: string;
  badge: "Active" | "Preview";
  route: string;
  enabled: boolean;
  inputs: ServiceInput[];
  ui?: {
    cardClass?: string;
    glowClass?: string;
    badgeClass?: string;
    titleClass?: string;
    ctaClass?: string;
    ctaLabel?: string;
  };
};

export const SERVICE_REGISTRY: ServiceConfig[] = [
  {
    id: "action_replica",
    title: "Action Replica",
    description: "Replace character identity while preserving motion and camera rhythm.",
    badge: "Active",
    route: "/workspace?service=action_replica",
    enabled: true,
    inputs: [
      { id: "character_image_url", type: "image", label: "Character Image" },
      { id: "source_video_url", type: "video", label: "Source Video" },
      { id: "preserve_camera", type: "select", label: "Preserve Camera" },
      { id: "preserve_motion", type: "select", label: "Preserve Motion" },
      { id: "preserve_timing", type: "select", label: "Preserve Timing" },
      { id: "prompt", type: "text", label: "Prompt" }
    ],
    ui: {
      cardClass:
        "group relative block rounded-2xl border border-rose-200/20 bg-gradient-to-br from-rose-950 via-slate-950 to-slate-950 p-8 overflow-hidden shadow-[0_18px_60px_rgba(244,63,94,0.18)] hover:shadow-[0_22px_80px_rgba(244,63,94,0.24)] hover:-translate-y-1 transition-all duration-300",
      glowClass:
        "pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-rose-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity",
      badgeClass:
        "text-[10px] font-bold text-rose-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider",
      titleClass: "text-2xl font-bold text-white mb-2 group-hover:text-rose-200 transition-colors",
      ctaClass:
        "relative z-10 flex items-center text-rose-200 font-medium text-sm group-hover:underline underline-offset-4",
      ctaLabel: "Enter Workspace"
    }
  },
  {
    id: "localization",
    title: "Video Localization",
    description: "Localize dialogue and lip-sync for new languages.",
    badge: "Preview",
    route: "/workspace?service=localization",
    enabled: true,
    inputs: [],
    ui: {
      cardClass:
        "group relative block rounded-2xl border border-slate-200/20 bg-gradient-to-br from-slate-950 via-slate-950 to-slate-950 p-8 overflow-hidden shadow-[0_18px_60px_rgba(15,23,42,0.18)] hover:shadow-[0_22px_80px_rgba(15,23,42,0.24)] hover:-translate-y-1 transition-all duration-300",
      glowClass:
        "pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-slate-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity",
      badgeClass:
        "text-[10px] font-bold text-slate-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider",
      titleClass: "text-2xl font-bold text-white mb-2 group-hover:text-slate-200 transition-colors",
      ctaClass:
        "relative z-10 flex items-center text-slate-200 font-medium text-sm group-hover:underline underline-offset-4",
      ctaLabel: "Enter Workspace"
    }
  },
  {
    id: "swap",
    title: "Swap",
    description: "Replace subject with target identity while preserving motion.",
    badge: "Active",
    route: "/workspace?service=swap",
    enabled: true,
    inputs: [
      { id: "source_video", type: "video", label: "Source Video" },
      { id: "target_image", type: "image", label: "Target Face" }
    ],
    ui: {
      cardClass:
        "group relative block rounded-2xl border border-emerald-200/20 bg-gradient-to-br from-emerald-950 via-slate-950 to-slate-950 p-8 overflow-hidden shadow-[0_18px_60px_rgba(16,185,129,0.18)] hover:shadow-[0_22px_80px_rgba(16,185,129,0.24)] hover:-translate-y-1 transition-all duration-300",
      glowClass:
        "pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-emerald-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity",
      badgeClass:
        "text-[10px] font-bold text-emerald-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider",
      titleClass: "text-2xl font-bold text-white mb-2 group-hover:text-emerald-200 transition-colors",
      ctaClass:
        "relative z-10 flex items-center text-emerald-200 font-medium text-sm group-hover:underline underline-offset-4",
      ctaLabel: "Enter Workspace"
    }
  },
  {
    id: "follow_video",
    title: "Follow Video",
    description: "Create a new video from one subject image and two reference videos.",
    badge: "Preview",
    route: "/workspace?service=follow_video",
    enabled: true,
    inputs: [
      { id: "subject_image", type: "image", label: "Subject Image" },
      { id: "reference_video_a", type: "video", label: "Reference Video A" },
      { id: "reference_video_b", type: "video", label: "Reference Video B" },
      { id: "prompt", type: "text", label: "Task Prompt" }
    ],
    ui: {
      cardClass:
        "group relative block rounded-2xl border border-cyan-200/20 bg-gradient-to-br from-cyan-950 via-slate-950 to-slate-950 p-8 overflow-hidden shadow-[0_18px_60px_rgba(34,211,238,0.18)] hover:shadow-[0_22px_80px_rgba(34,211,238,0.24)] hover:-translate-y-1 transition-all duration-300",
      glowClass:
        "pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-cyan-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity",
      badgeClass:
        "text-[10px] font-bold text-cyan-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider",
      titleClass: "text-2xl font-bold text-white mb-2 group-hover:text-cyan-200 transition-colors",
      ctaClass:
        "relative z-10 flex items-center text-cyan-200 font-medium text-sm group-hover:underline underline-offset-4",
      ctaLabel: "Enter Workspace"
    }
  }
];
