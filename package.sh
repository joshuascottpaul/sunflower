#!/bin/bash
set -e
VERSION=${1:-"v0.1.0"}
PLATFORMS=("linux-amd64" "linux-arm64" "darwin-amd64" "darwin-arm64")
echo "Packaging sunflower $VERSION"
mkdir -p dist
for platform in "${PLATFORMS[@]}"; do
    platform_dir="dist/sunflower-$platform"
    mkdir -p "$platform_dir"
    cp sun_mimic.py "$platform_dir/"
    cp requirements.txt config.json.example .env.example "$platform_dir/" 2>/dev/null || true
    cp README.md LICENSE "$platform_dir/" 2>/dev/null || true
    [ -d "scripts" ] && cp -r scripts "$platform_dir/" || true
    [ -d "launchd" ] && cp -r launchd "$platform_dir/" || true
    cat > "$platform_dir/install.sh" << 'INSTALL'
#!/bin/bash
set -e
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$INSTALL_DIR"
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required"
    exit 1
fi
pip3 install -r requirements.txt --user
cp sun_mimic.py "$INSTALL_DIR/sunflower"
chmod +x "$INSTALL_DIR/sunflower"
echo "✓ Installed to $INSTALL_DIR/sunflower"
INSTALL
    chmod +x "$platform_dir/install.sh"
    cd dist
    tar -czf "sunflower-$VERSION-$platform.tar.gz" "sunflower-$platform"
    rm -rf "sunflower-$platform"
    cd ..
done
echo "✓ All packages created in dist/"
