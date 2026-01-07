import { TaskRecord } from "../lib/api";
import { resolveAssetUrl } from "../lib/url";
import VideoPlayer from "./VideoPlayer";

type TaskDetailProps = {
  task: TaskRecord | null;
};

export default function TaskDetail({ task }: TaskDetailProps) {
  const thumbnailUrl = resolveAssetUrl(task?.thumb_url);
  const outputUrl = resolveAssetUrl(task?.output_url);
  const metadataWidth = task?.metadata?.width;
  const metadataHeight = task?.metadata?.height;
  const metadataDuration = task?.metadata?.duration;
  const widthLabel = typeof metadataWidth === "number" ? metadataWidth : "--";
  const heightLabel = typeof metadataHeight === "number" ? metadataHeight : "--";
  const durationLabel =
    typeof metadataDuration === "number" ? metadataDuration.toFixed(2) : "--";

  return (
    <div className="mt-6 grid gap-4 md:grid-cols-2">
      <div className="rounded-xl border border-white/10 bg-black/40 p-4">
        <h3 className="text-sm font-semibold">Input Snapshot</h3>
        {thumbnailUrl ? (
          <img
            src={thumbnailUrl}
            alt="thumbnail"
            className="mt-3 h-40 w-full rounded-lg object-cover"
          />
        ) : (
          <div className="mt-3 flex h-40 items-center justify-center rounded-lg border border-dashed border-white/20 text-xs text-white/40">
            Thumbnail pending
          </div>
        )}
        {!thumbnailUrl && task ? (
          <p className="mt-2 text-xs text-amber-200/80">
            Thumbnail URL unavailable.
          </p>
        ) : null}
        <div className="mt-3 text-xs text-white/60">
          <p>
            Duration: {durationLabel}s
          </p>
          <p>
            Resolution: {widthLabel}x{heightLabel}
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-white/10 bg-black/40 p-4">
        <h3 className="text-sm font-semibold">Output Preview</h3>
        {task?.output_url ? (
          <div className="mt-3">
            <VideoPlayer
              className="h-40 w-full rounded-lg bg-black"
              src={task?.output_url}
            />
            {outputUrl ? (
              <a
                href={outputUrl}
                download
                className="mt-3 inline-flex text-xs text-emerald-200 hover:text-emerald-100"
              >
                Download preset
              </a>
            ) : null}
          </div>
        ) : (
          <div className="mt-3 flex h-40 items-center justify-center rounded-lg border border-dashed border-white/20 text-xs text-white/40">
            Output pending
          </div>
        )}
        {!outputUrl && task ? (
          <p className="mt-2 text-xs text-amber-200/80">
            Output URL not available yet.
          </p>
        ) : null}
      </div>
    </div>
  );
}
