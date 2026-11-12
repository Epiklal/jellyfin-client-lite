#!/bin/sh
# Baut dist/jellyfin-client-lite_<version>_all.deb aus diesem Quellordner.
set -eu
umask 022
cd "$(dirname "$0")"
name=jellyfin-client-lite
version=$(python3 -c 'import jfclite; print(jfclite.__version__)')
metainfo=packaging/io.github.epiklal.jellyfin_client_lite.metainfo.xml
if ! grep -q "<release version=\"$version\"" "$metainfo"; then
    echo "Version $version fehlt in $metainfo unter <releases>" >&2
    exit 1
fi
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
chmod 755 "$root"  # mktemp legt 700 an - landet sonst als Rechte für / im Paket

install -Dm755 packaging/$name "$root/usr/bin/$name"
install -Dm644 run.py config.example.json requirements.txt -t "$root/usr/lib/$name"
install -Dm644 jfclite/*.py -t "$root/usr/lib/$name/jfclite"
install -Dm644 packaging/$name.desktop -t "$root/usr/share/applications"
install -Dm644 packaging/$name.svg -t "$root/usr/share/icons/hicolor/scalable/apps"
install -Dm644 "$metainfo" -t "$root/usr/share/metainfo"
# Eigene Paketquelle: nach der ersten Installation kommen Updates über die
# normale Aktualisierung des Systems (wie bei Chrome oder VS Code)
install -Dm644 packaging/$name-archive.gpg -t "$root/usr/share/keyrings"
install -Dm644 packaging/$name.sources -t "$root/etc/apt/sources.list.d"
install -Dm644 README.md -t "$root/usr/share/doc/$name"
cat > "$root/usr/share/doc/$name/copyright" <<COPYRIGHT
Copyright 2026 ketchupboy (Epiklal)
Source: https://github.com/Epiklal/jellyfin-client-lite
License: Apache-2.0
 Der vollständige Lizenztext steht in /usr/share/common-licenses/Apache-2.0.
COPYRIGHT
install -Dm644 NOTICE -t "$root/usr/share/doc/$name"

mkdir -p "$root/DEBIAN"
sed "s/@VERSION@/$version/" packaging/debian/control.in > "$root/DEBIAN/control"
install -m755 packaging/debian/postinst "$root/DEBIAN/postinst"
echo "/etc/apt/sources.list.d/$name.sources" > "$root/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group --build "$root" "dist/${name}_${version}_all.deb"
