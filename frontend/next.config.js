/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: "https://api.swiftcraft.ai/api/v1/:path*"
      }
    ];
  }
};

module.exports = nextConfig;
