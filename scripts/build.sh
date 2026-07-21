#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SIGN_IDENTITY="smartgym-codesign"
SIGN_IDENTIFIER="com.sla1k.smartgym-mcp"
INSTALL_DIR="$HOME/.local/share/smartgym-mcp"

uv run --with pyinstaller pyinstaller --noconfirm smartgym-mcp.spec

# Post-build re-sign with a stable identity + identifier so macOS TCC grants
# survive rebuilds. Do NOT move this into the spec's codesign_identity: that
# forces hardened runtime, which breaks self-signed certs (library validation).
codesign --force --identifier "$SIGN_IDENTIFIER" --sign "$SIGN_IDENTITY" \
    dist/smartgym-mcp/smartgym-mcp
codesign --verify --strict dist/smartgym-mcp/smartgym-mcp

mkdir -p "$INSTALL_DIR"
rsync -a --delete dist/smartgym-mcp/ "$INSTALL_DIR/"

codesign -dv "$INSTALL_DIR/smartgym-mcp" 2>&1 | grep -E "^(Identifier|Authority|Signature)" || true
echo "Installed to $INSTALL_DIR"
