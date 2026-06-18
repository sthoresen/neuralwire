import type { NextConfig } from "next";

// Local dev proxies /api/* to the backend so the browser talks to the dev
// server (same-origin) and never hits CORS. In production NEXT_PUBLIC_API_URL
// points straight at the backend, so these rewrites are never matched there.
const API_PROXY_TARGET =
  process.env.API_PROXY_TARGET ?? "https://web-production-3adb4.up.railway.app";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY_TARGET}/:path*`,
      },
    ];
  },
};

export default nextConfig;
