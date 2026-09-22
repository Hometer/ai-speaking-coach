import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 允许局域网其它设备访问 dev 服务（否则 dev 资源会被当跨源拦截）
  allowedDevOrigins: ["192.168.110.24"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
