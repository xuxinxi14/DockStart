import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: {
      // 两类会被写入并短暂锁定的路径必须排除，否则监视器命中 EBUSY 会让
      // beforeDevCommand 非零退出、整个 tauri dev 中止：
      //   1) src-tauri/target —— Rust 编译输出（Tauri 官方模板同样排除）
      //   2) *.tmpdir / *.tmp —— 编辑器保存文件时的临时目录与临时文件
      ignored: [
        "**/src-tauri/**",
        "**/node_modules/**",
        "**/.git/**",
        "**/*.tmpdir/**",
        "**/*.tmp",
      ],
    },
  },
});
