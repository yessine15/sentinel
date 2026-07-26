/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output: Next.js bundles everything into .next/standalone
  // so the Docker image only needs `node server.js`.
  output: "standalone",

  // In dev we proxy /api/* to the local FastAPI server so the browser
  // WebSocket connects same-origin. In production the Ingress routes
  // WebSocket and API calls to the backend service.
  async rewrites() {
    if (process.env.NODE_ENV === "development") {
      return [
        {
          source: "/api/:path*",
          destination: "http://localhost:8000/:path*",
        },
        {
          source: "/chat/:path*",
          destination: "http://localhost:8000/chat/:path*",
        },
      ];
    }
    return [];
  },
};

export default nextConfig;
