import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["clsx", "date-fns", "recharts"],
};

export default nextConfig;
