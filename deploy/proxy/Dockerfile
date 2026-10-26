# syntax=docker/dockerfile:1.7
# TLS-terminating reverse proxy (ADR 0029): Caddy with certificates from a public ACME CA.
# Base image pinned by tag; Renovate pins and updates its digest.
# The release binary of the official image lags behind Go security fixes, so Caddy is rebuilt
# with the builder image's current Go toolchain and a patched golang.org/x/net. Drop the
# replace once Caddy's own go.mod carries x/net v0.60.0 or later.
FROM caddy:2.11.7-builder-alpine AS build
RUN xcaddy build v2.11.7 \
      --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
      --output /out/caddy

FROM caddy:2.11.7-alpine
# curl is not used (the health check uses busybox wget) and carries its own CVEs.
# Caddy runs as uid 10001 on unprivileged ports (8080/8443, published as 80/443). The file
# capability on the binary is removed: with cap_drop ALL and no-new-privileges the kernel
# refuses to exec a binary that asks for a capability it cannot get.
COPY --from=build /out/caddy /usr/bin/caddy
RUN setcap -r /usr/bin/caddy \
 && apk del --no-cache curl libcurl brotli-libs \
 && chown -R 10001:10001 /data /config
COPY Caddyfile /etc/caddy/Caddyfile
USER 10001:10001
EXPOSE 8080 8443
# Without a domain Caddy would serve plain HTTP on 8443, so an empty value stops the container.
CMD ["sh", "-c", "test -n \"$TLS_DOMAIN\" && test -n \"$ACME_EMAIL\" || { echo 'TLS_DOMAIN and ACME_EMAIL are required' >&2; exit 1; }; exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:2019/config/"]
