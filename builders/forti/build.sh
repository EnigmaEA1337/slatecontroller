#!/bin/sh
# Build openfortivpn as a fully static aarch64-musl binary.
#
# Inputs (env, all optional) :
#   OPENFORTIVPN_REF   git ref to checkout (default: v1.21.0)
#   OUT_DIR            where to put the final binary (default: /out)
#
# The static-link strategy lets us drop the binary on the Slate without
# worrying about libc / libssl version skew with the firmware. Trade-off
# is +2 MB (~3 MB total) which is fine on a router with 32 GB storage.

set -eu

REF="${OPENFORTIVPN_REF:-v1.21.0}"
OUT_DIR="${OUT_DIR:-/out}"
SRC_DIR="/work/openfortivpn"

mkdir -p "$OUT_DIR"

echo "[build] cloning openfortivpn $REF"
rm -rf "$SRC_DIR"
git clone --depth 1 --branch "$REF" \
  https://github.com/adrienverge/openfortivpn.git "$SRC_DIR"

cd "$SRC_DIR"

echo "[build] autoreconf"
autoreconf -fi >/dev/null

# The configure script doesn't auto-pickup the cross-compiled openssl in
# /opt/ssl. We feed it via PKG_CONFIG_PATH + LDFLAGS so libcrypto/libssl
# both resolve to the static .a we baked into the image.
export PATH="/opt/ssl/bin:$PATH"
export PKG_CONFIG_PATH="/opt/ssl/lib/pkgconfig:/opt/ssl/lib64/pkgconfig"
export PKG_CONFIG_LIBDIR="/opt/ssl/lib/pkgconfig:/opt/ssl/lib64/pkgconfig"
export PKG_CONFIG="pkg-config --static"
export CPPFLAGS="-I/opt/ssl/include"
# -static : link our own deps statically.
# -static-libgcc : drop the libgcc_s.so.1 dep too.
# Order matters : libssl needs libcrypto, libcrypto needs libdl/pthread.
export LDFLAGS="-static -static-libgcc -L/opt/ssl/lib64 -L/opt/ssl/lib"
export LIBS="-lssl -lcrypto -lpthread -ldl"

echo "[build] configure"
# Autoconf needs --host=<triplet> to switch into cross-compile mode (it
# refuses to execute test binaries otherwise — they're aarch64, host is
# x86_64). The muslcc image's `gcc` IS the aarch64-musl cross compiler
# but has no `aarch64-linux-musl-` prefix, so we force autoconf to use
# the unprefixed names via CC/AR/RANLIB/STRIP env vars.
CC=gcc \
AR=ar \
RANLIB=ranlib \
STRIP=strip \
ac_cv_file__proc_net_route=yes \
ac_cv_file__usr_sbin_ppp=yes \
ac_cv_file__usr_sbin_pppd=yes \
ac_cv_file__sbin_pppd=yes \
./configure \
  --host=aarch64-linux-musl \
  --build=x86_64-linux-musl \
  --disable-shared \
  --enable-static \
  --sysconfdir=/etc \
  --prefix=/usr \
  --with-pppd=/usr/sbin/pppd

echo "[build] make"
make -j"$(nproc)"

echo "[build] strip"
strip openfortivpn

echo "[build] verify static link"
if readelf -d openfortivpn | grep -E "NEEDED" >/dev/null 2>&1 ; then
  echo "ERROR: binary still has dynamic dependencies :" >&2
  readelf -d openfortivpn | grep NEEDED >&2
  exit 1
fi

# Can't ./openfortivpn --version — we're on x86_64, the binary is
# aarch64. Use the git ref the operator asked for as the manifest's
# version field ; the Slate's preflight will surface the actual --version
# string once the binary is sideloaded.
VERSION="$REF"
SIZE=$(stat -c %s openfortivpn)
SHA=$(sha256sum openfortivpn | awk '{print $1}')

echo "[build] copying to $OUT_DIR/openfortivpn"
cp openfortivpn "$OUT_DIR/openfortivpn"

cat >"$OUT_DIR/build.json" <<EOF
{
  "version": "$VERSION",
  "git_ref": "$REF",
  "size_bytes": $SIZE,
  "sha256": "$SHA",
  "target_arch": "aarch64-linux-musl",
  "static": true
}
EOF

echo "[build] DONE — $VERSION ($SIZE bytes, sha256=$SHA)"
