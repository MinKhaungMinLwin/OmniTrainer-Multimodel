# Retail web app for Cloud Run: a static Vite build served by nginx, which also
# proxies /api to the API service so the browser stays same-origin (no CORS).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY apps/mobile/package.json apps/mobile/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN npm ci --workspace @omni/web --include-workspace-root --no-audit --no-fund
COPY tsconfig.json ./
COPY packages ./packages
COPY apps/web ./apps/web
ARG VITE_PRODUCT_EDITION=retail
ENV VITE_PRODUCT_EDITION=$VITE_PRODUCT_EDITION VITE_API_URL=""
RUN npm run build --workspace @omni/web

FROM nginx:1.29-alpine
COPY deploy/gcp/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
ENV PORT=8080
EXPOSE 8080
