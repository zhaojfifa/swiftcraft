export function resolveAssetUrl(u?: string | null): string | null {
  if (u === null || u === undefined) {
    return null;
  }
  if (u.trim() === "") {
    return null;
  }
  if (u.startsWith("http://") || u.startsWith("https://")) {
    return u;
  }
  if (u.startsWith("/")) {
    return `${process.env.NEXT_PUBLIC_API_BASE || ""}${u}`;
  }
  return u;
}
