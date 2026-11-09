/** @type {import('next').NextConfig} */
// Read at BUILD time: Next bakes rewrite destinations into .next/routes-manifest.json, so in Docker
// BACKEND_URL must be passed as a build arg (see frontend/Dockerfile), not only at runtime.
// A bare host or host:port (e.g. Render's `hostport`) is accepted and treated as http.
const RAW_BACKEND = (process.env.BACKEND_URL || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
const BACKEND = /^https?:\/\//i.test(RAW_BACKEND) ? RAW_BACKEND : `http://${RAW_BACKEND}`;

const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${BACKEND}/api/v1/:path*` }
    ];
  },
};

export default nextConfig;
