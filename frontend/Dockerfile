# Dev-mode image only — runs the Vite dev server, not a production build.
# Source is bind-mounted in by compose.yml for hot reload; this image just
# provides the node_modules (installed at build time, kept out of the mount
# by an anonymous volume in compose.yml so the host's own node_modules,
# if any, doesn't shadow it).
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
