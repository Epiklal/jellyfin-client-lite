#!/bin/sh
# Baut dist/jellyfin-client-lite_<version>_all.deb aus diesem Quellordner.
set -eu
umask 022
cd "$(dirname "$0")"
name=jellyfin-client-lite
version=$(python3 -c 'import jfclite; print(jfclite.__version__)')
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
chmod 755 "$root"  # mktemp legt 700 an - landet sonst als Rechte für / im Paket

install -Dm755 packaging/$name "$root/usr/bin/$name"
install -Dm644 run.py config.example.json -t "$root/usr/lib/$name"
install -Dm644 jfclite/*.py -t "$root/usr/lib/$name/jfclite"
install -Dm644 packaging/$name.desktop -t "$root/usr/share/applications"
install -Dm644 packaging/$name.svg -t "$root/usr/share/icons/hicolor/scalable/apps"
install -Dm644 README.md -t "$root/usr/share/doc/$name"
cat > "$root/usr/share/doc/$name/copyright" <<COPYRIGHT
Copyright (C) 2026 ketchupboy
License: GPL-3+
 Der vollständige Lizenztext steht in /usr/share/common-licenses/GPL-3.
COPYRIGHT

mkdir -p "$root/DEBIAN"
sed "s/@VERSION@/$version/" packaging/debian/control.in > "$root/DEBIAN/control"
install -m755 packaging/debian/postinst "$root/DEBIAN/postinst"

mkdir -p dist
dpkg-deb --root-owner-group --build "$root" "dist/${name}_${version}_all.deb"
