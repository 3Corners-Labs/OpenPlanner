# syntax=docker/dockerfile:1.7
# TLS-terminating reverse proxy (ADR 0030): Caddy with certificates from a public ACME CA.
# Base image pinned by tag; Renovate pins and updates its digest.
# The release binary of the official image lags behind Go security fixes, so Caddy is rebuilt
# with the builder image's current Go toolchain and patched dependencies. Drop each replace
# once Caddy's own go.mod carries that version or later: golang.org/x/net v0.60.0, and
# github.com/go-jose/go-jose/v4 v4.1.6 (CVE-2026-116587, DoS when parsing JWKs with RSA keys).
# The build fetches modules from proxy.golang.org and verifies them against sum.golang.org,
# which now and then fails with a transient HTTP/2 stream error. The build is therefore tried
# up to 3 times (waits of 10 s and 30 s); the module cache is a BuildKit cache mount, so a retry
# only fetches what is still missing. Checksum verification stays on: never set GOSUMDB=off,
# GONOSUMDB, GONOSUMCHECK or GOFLAGS=-insecure here.
FROM caddy:2.11.7-builder-alpine AS build
RUN --mount=type=cache,target=/go/pkg/mod \
    attempt=1; \
    until xcaddy build v2.11.7 \
        --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
        --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6 \
        --output /out/caddy; do \
      if [ "$attempt" -ge 3 ]; then echo "xcaddy build failed after $attempt attempts" >&2; exit 1; fi; \
      delay=10; if [ "$attempt" -eq 2 ]; then delay=30; fi; \
      echo "xcaddy build attempt $attempt failed; retrying in ${delay}s" >&2; \
      sleep "$delay"; attempt=$((attempt + 1)); \
    done

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
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
USER 10001:10001
EXPOSE 8080 8443
# The entrypoint checks the settings (a domain, and a mailbox or an own certificate; ADR 0064)
# and refuses to start otherwise: without them Caddy would serve plain HTTP on 8443.
CMD ["/usr/local/bin/entrypoint.sh"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:2019/config/"]
