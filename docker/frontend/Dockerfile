# ---------- stage 1: build ----------
FROM node:20-alpine AS build

WORKDIR /build

# Lockfile-first so a dependency change busts the cache and a source-only change does not.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# VITE_API_PROXY_TARGET only affects the dev server; the production bundle is
# same-origin by design (see src/lib/api.js and nginx.conf), so there is no API URL
# to bake in and the image is environment-independent.
#
# VITE_DEMO_MODE is the one exception, and it has to be a build argument because Vite
# inlines it at build time. It gates the "Continue as the demo commander" shortcut on the
# sign-in screen, which ships the seeded fixture credentials to anyone who loads the page.
# Unset — the default — means the shortcut is absent, so a public deployment cannot leak
# them. A local `make up` can set VITE_DEMO_MODE=true to keep the one-click demo.
ARG VITE_DEMO_MODE=false
ENV VITE_DEMO_MODE=${VITE_DEMO_MODE}
RUN npm run build


# ---------- stage 2: serve ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="Fleet Digital Twin Web" \
      org.opencontainers.image.description="Static React build + same-origin BFF proxy to the API"

# Host of the API monolith. `api` under Compose; override to point elsewhere.
ENV FDT_API_HOST=api

# Must land in /etc/nginx/templates, not conf.d: the image's envsubst pass reads
# NGINX_ENVSUBST_TEMPLATE_DIR (default /etc/nginx/templates) and writes the result into
# conf.d. Dropped straight into conf.d it would be served as an empty site and
# `server ${FDT_API_HOST}:8000` would never be substituted.
COPY docker/frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /build/dist /usr/share/nginx/html

# unprivileged image: 8080 as non-root, no chown needed
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]