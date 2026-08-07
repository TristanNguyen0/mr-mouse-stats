/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: the build produces plain files for S3 behind CloudFront.
  // There is no Node server in this architecture — all data comes from the
  // public API at runtime, so nothing here is rendered on a server.
  output: "export",

  // CloudFront serves /players/ -> /players/index.html.
  trailingSlash: true,

  images: { unoptimized: true },

  // Fail the build on type errors rather than shipping them.
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
