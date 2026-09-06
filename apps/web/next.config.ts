import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit .next/standalone for the Docker runner stage.
  output: "standalone",
  // Pin the tracing root to this app so the standalone layout is the same
  // whether building inside the monorepo or the Docker context (which only
  // contains apps/web). Without this, Next infers the workspace root and
  // nests server.js under standalone/apps/web/.
  outputFileTracingRoot: __dirname,
  // Next 16 builds with Turbopack, which resolves its own root and ignores
  // outputFileTracingRoot. Without this the build aborts with "Next.js
  // inferred your workspace root, but it may not be correct".
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
