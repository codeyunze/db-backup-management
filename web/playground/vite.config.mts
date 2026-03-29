import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api/db-instances': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api/backup-jobs': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api/backup-files': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api/auth': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api/user': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          // 后端权限模式拉菜单；须先于通用 /api 代理，否则 mock 无法用本地 token 校验 JWT
          '/api/menu': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          // 系统管理（角色/菜单/部门等）mock 只认 JWT；走 Flask 时用本地 access token 校验
          '/api/system': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api/timezone': {
            target: 'http://127.0.0.1:8081',
            changeOrigin: true,
            ws: false,
          },
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            // mock代理目标地址
            target: 'http://localhost:5320/api',
            ws: true,
          },
        },
      },
    },
  };
});
