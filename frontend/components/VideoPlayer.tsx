import { resolveAssetUrl } from "../lib/url";

type VideoPlayerProps = {
  src?: string | null;
  className?: string;
};

export default function VideoPlayer({ src, className }: VideoPlayerProps) {
  const resolvedSrc = resolveAssetUrl(src);

  if (!resolvedSrc) {
    return (
      <div
        className={`flex h-40 w-full items-center justify-center rounded-lg border border-dashed border-white/20 text-xs text-white/40 ${
          className || ""
        }`}
      >
        Video source unavailable
      </div>
    );
  }

  return (
    <video controls className={className} src={resolvedSrc}>
      Your browser does not support the video tag.
    </video>
  );
}
