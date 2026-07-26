# =============================================================
# Dockerfile for Sentinel Frontend (Next.js 15)
#
# Multi-stage build:
#   1. builder — install all deps + compile Next.js standalone
#   2. runtime — run the standalone Node.js server
#
# Image: ghcr.io/yessine15/sentinel-frontend:<tag>
# =============================================================

# ---- Stage 1: builder ---------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

# Install all deps (including devDependencies — Tailwind etc.
# are needed for the build step).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy source and build.  NEXT_PUBLIC_WS_URL is baked at build
# time; leave it empty for same-origin production use.
COPY frontend/ .
ENV NEXT_PUBLIC_WS_URL=""

RUN npm run build

# ---- Stage 2: runtime ---------------------------------------
FROM node:22-alpine AS runtime
WORKDIR /app

# Create non-root user (Alpine uses BusyBox adduser/addgroup).
RUN addgroup -S -g 1001 sentinel \
    && adduser -S -u 1001 -G sentinel sentinel

# Next.js standalone output — three pieces:
#   1. standalone/  → server.js + all server bundles
#   2. .next/static/ → client-side JS/CSS loaded by the browser
#   3. public/       → static assets (favicon, robots.txt, …)
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER sentinel
EXPOSE 3000

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
