#!/usr/bin/env bash
# Downloads and installs the RINGO binary (GSI, Japan -- RINEX QC engine
# used by geode.rinex_qc, see knowledge/decisions/0005-switch-to-ringo.md).
# RINGO ships as a single statically-linked binary per platform, no build
# step and no other runtime dependency, so this is a plain
# download-unpack-chmod rather than a package manager recipe.
#
# Usage: scripts/install_ringo.sh [version] [install_dir]
#   version:     RINGO release to install (default: 0.9.6)
#   install_dir: where to place the binary (default: $HOME/.local/bin)
#
# Re-running with the same arguments is harmless (re-downloads and
# overwrites). The binary is fetched fresh from GSI's own site each time
# rather than vendored into this repo, since GSI publishes no explicit
# software license for RINGO (only a general website Terms of Use) --
# each install pulls directly from GSI under GSI's own terms.
set -euo pipefail

VERSION="${1:-0.9.6}"
INSTALL_DIR="${2:-$HOME/.local/bin}"
BASE_URL="https://terras.gsi.go.jp/software/ringo/dist/v${VERSION}"

case "$(uname -s)" in
    Linux)  os=linux ;;
    Darwin) os=macos ;;
    FreeBSD) os=freebsd ;;
    SunOS)  os=solaris ;;
    *) echo "install_ringo.sh: unsupported OS $(uname -s) -- download manually from" \
            "https://terras.gsi.go.jp/software/ringo/en/downloads" >&2; exit 1 ;;
esac

case "$(uname -m)" in
    x86_64|amd64) arch=64 ;;
    aarch64|arm64) arch=arm64 ;;
    armv7l|armv6l) arch=arm ;;
    i386|i686) arch=32 ;;
    *) echo "install_ringo.sh: unsupported architecture $(uname -m) -- download manually from" \
            "https://terras.gsi.go.jp/software/ringo/en/downloads" >&2; exit 1 ;;
esac

# GSI's own asset naming: linux64/linux32/linux_arm64/linux_arm, but
# macos_amd64/macos_arm64 (and freebsd_amd64/solaris_amd64, 64-bit only).
case "$os" in
    linux)
        case "$arch" in
            64) platform="linux64" ;;
            32) platform="linux32" ;;
            arm64) platform="linux_arm64" ;;
            arm) platform="linux_arm" ;;
        esac
        ;;
    macos)
        case "$arch" in
            64) platform="macos_amd64" ;;
            arm64) platform="macos_arm64" ;;
            *) echo "install_ringo.sh: no macOS $arch build published" >&2; exit 1 ;;
        esac
        ;;
    freebsd) platform="freebsd_amd64" ;;
    solaris) platform="solaris_amd64" ;;
esac

archive="ringo-v${VERSION}-${platform}.tgz"
url="${BASE_URL}/${archive}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo " >> Downloading $url"
curl -fsSL -o "$tmpdir/$archive" "$url"

tar xzf "$tmpdir/$archive" -C "$tmpdir"

binary="$(find "$tmpdir" -type f -name ringo | head -n1)"
if [ -z "$binary" ]; then
    echo "install_ringo.sh: no 'ringo' binary found inside $archive" >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"
install -m 755 "$binary" "$INSTALL_DIR/ringo"

echo " >> Installed ringo v${VERSION} to $INSTALL_DIR/ringo"
"$INSTALL_DIR/ringo" --version

case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *) echo " >> NOTE: $INSTALL_DIR is not on your PATH. Add this to your shell profile:" \
            "export PATH=\"$INSTALL_DIR:\$PATH\"" ;;
esac
