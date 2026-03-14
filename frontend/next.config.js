/** @type {import('next').NextConfig} */
const rawApiBase = (process.env.NEXT_PUBLIC_API_BASE || "https://api.swiftcraft.ai").replace(/\/+$/, "");
const apiPrefix = rawApiBase.endsWith("/api/v1") ? rawApiBase : `${rawApiBase}/api/v1`;

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiPrefix}/:path*`
      }
    ];
  }
};

module.exports = nextConfig;
