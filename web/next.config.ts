import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Overridable so a second build (e.g. a verification run) doesn't share .next with `next dev`.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // The Docker image runs the standalone server (web/Dockerfile); `next start` doesn't use it.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
