// One place for the two ports: vite proxies /api and /files to the server.
// STUDIO_WEB_PORT / STUDIO_SERVER_PORT override them, so a second copy (a test run in fake mode, say) can run
// beside the one in use.
export const SERVER_HOST = '127.0.0.1';
export const SERVER_PORT = Number(process.env.STUDIO_SERVER_PORT) || 5392;
export const WEB_PORT = Number(process.env.STUDIO_WEB_PORT) || 5391;
