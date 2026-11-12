#!/bin/sh
# Von der GitHub Action (.github/workflows/release.yml) aufgerufen: legt das
# neue .deb in die Paketquelle auf dem Zweig gh-pages (GitHub Pages) und lädt
# sie hoch. Braucht GITHUB_TOKEN, GITHUB_REPOSITORY und den Signierschlüssel
# im gpg (aus dem Secret APT_SIGNING_KEY).
set -eu
deb="$1"
name=jellyfin-client-lite
# PUBLISH_REMOTE: anderes Ziel, z.B. ein lokales Test-Repo
remote="${PUBLISH_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"
site=$(mktemp -d)
trap 'rm -rf "$site"' EXIT

if git ls-remote --exit-code --heads "$remote" gh-pages >/dev/null 2>&1; then
    git clone -q --depth 1 --branch gh-pages "$remote" "$site"
else
    git init -q -b gh-pages "$site"
    git -C "$site" remote add origin "$remote"
fi

mkdir -p "$site/apt/pool/main"
cp "$deb" "$site/apt/pool/main/"
# Nur die letzten drei Versionen behalten - apt braucht ohnehin nur die neueste
ls -1 "$site"/apt/pool/main/*.deb | sort -V | head -n -3 | xargs -r rm -f
python3 packaging/apt-repo.py "$site/apt"

# Feste Adressen: die neueste Version, der Schlüssel, das Bild für Discover
cp "$deb" "$site/$name.deb"
cp packaging/$name-archive.asc "$site/key.asc"
cp packaging/screenshot.png "$site/screenshot.png"
cp packaging/site/index.html "$site/index.html"
touch "$site/.nojekyll"  # Ordner wie "dists" unverändert ausliefern

git -C "$site" add -A
git -C "$site" -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Paketquelle: $(basename "$deb")"
git -C "$site" push -q origin gh-pages
echo "Veröffentlicht: https://epiklal.github.io/$name/"
