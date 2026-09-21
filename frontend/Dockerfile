# syntax=docker/dockerfile:1
FROM node:24.13.1-alpine AS build

ENV PNPM_HOME="/pnpm" \
    PATH="$PNPM_HOME:$PATH" \
    NODE_ENV=production

WORKDIR /app
RUN corepack enable && corepack prepare pnpm@12.4.1 --activate

# The lockfile is the sole source for the installed dependency graph.
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm lint && pnpm build

FROM caddy:2.11.4-alpine AS runtime

RUN setcap -r /usr/bin/caddy \
    && addgroup -S -g 10001 sentinelai \
    && adduser -S -D -H -u 10001 -G sentinelai sentinelai

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy
RUN chown -R sentinelai:sentinelai /etc/caddy /usr/share/caddy

USER sentinelai
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8080/ > /dev/null || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
