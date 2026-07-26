/** @type {import('next').NextConfig} */
const nextConfig = {
  // The API runs on localhost:8000 in dev.  We proxy /chat/ws to it
  // so the browser WebSocket connects to the same origin.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
