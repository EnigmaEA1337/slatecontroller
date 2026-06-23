# Cross-compile openfortivpn statically for aarch64/musl, the libc the
# Slate 7 Pro firmware (OpenWrt 21.02 + GL.iNet 4.x, target
# mediatek/mt7987, arch aarch64_cortex-a53) actually runs.
#
# Image stays toolchain-only — the openfortivpn version is cloned at
# runtime by build.sh so bumping the upstream version doesn't require
# rebuilding the base image. Build artifact ends up in /out (mounted
# in by the caller).
#
# Note on muslcc/x86_64:aarch64-linux-musl :
#   The image ships ONLY the cross-compiler — its `gcc`, `ar`, `ranlib`,
#   `strip` ARE the aarch64-linux-musl variants. There is no host gcc.
#   So we invoke `gcc` directly without an arch prefix. Same goes for
#   build.sh (no aarch64-linux-musl-strip either, just `strip`).
FROM muslcc/x86_64:aarch64-linux-musl

# Tools needed by the openfortivpn autotools chain + a curl that can
# fetch tarballs. perl is required by openssl's Configure script.
RUN apk add --no-cache \
      autoconf \
      automake \
      bash \
      curl \
      git \
      libtool \
      linux-headers \
      make \
      perl \
      pkgconfig

# Pre-build a static openssl for aarch64-musl so the build.sh doesn't
# repeat that ~5 min step on every run. Pinned to a recent LTS branch.
# /opt/ssl/{lib,include} are picked up via CPPFLAGS/LDFLAGS in build.sh.
ARG OPENSSL_VERSION=3.0.13
RUN set -eux \
 && mkdir -p /tmp/ssl-build && cd /tmp/ssl-build \
 && curl -fsSL "https://www.openssl.org/source/openssl-${OPENSSL_VERSION}.tar.gz" \
      -o openssl.tar.gz \
 && tar -xzf openssl.tar.gz --strip-components=1 \
 && ./Configure linux-aarch64 \
      --prefix=/opt/ssl \
      no-shared no-tests no-dso no-engine \
 && make -j"$(nproc)" build_libs \
 && make install_dev \
 && cd / && rm -rf /tmp/ssl-build

WORKDIR /work
COPY build.sh /usr/local/bin/build-openfortivpn
RUN chmod +x /usr/local/bin/build-openfortivpn

# Default command — the caller mounts /out and we drop the static binary
# there. Stdout/stderr are streamed back to the controller endpoint.
CMD ["/usr/local/bin/build-openfortivpn"]
