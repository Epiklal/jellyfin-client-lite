#!/usr/bin/env python3
"""Baut die APT-Paketquelle (GitHub Pages) aus den .deb-Dateien in <apt>/pool.

    python3 packaging/apt-repo.py <apt-ordner>

Legt dists/stable an: Paketlisten für jede gängige Architektur (das Paket ist
"all", apt sucht aber nur unter der eigenen Architektur), dazu die AppStream-
Daten (DEP-11), damit KDE Discover und GNOME Software das Programm mit Symbol
und Beschreibung finden. Signiert wird mit dem Schlüssel im Standard-gpg
(in der GitHub Action aus dem Secret APT_SIGNING_KEY importiert).

Braucht: apt-ftparchive (apt-utils), gpg, rsvg-convert (librsvg2-bin) oder ImageMagick.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "jellyfin-client-lite"
METAINFO = HERE / "io.github.epiklal.jellyfin_client_lite.metainfo.xml"
ICON_SVG = HERE / f"{NAME}.svg"
SUITE, COMPONENT = "stable", "main"
ARCHES = ["amd64", "arm64", "armhf", "i386", "riscv64", "ppc64el", "s390x"]
ICON_SIZES = [48, 64, 128]
LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def run(*cmd: str, cwd: Path | None = None) -> bytes:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True).stdout


def write_gz(path: Path, data: bytes) -> None:
    """Schreibt <path> (mit .gz) und daneben die unkomprimierte Datei: apt lädt
    die DEP-11-Daten nur, wenn das Release auch den unkomprimierten Namen
    aufführt (wie bei Debian), sonst übergeht es sie stillschweigend."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix("").write_bytes(data)
    # mtime=0: gleiche Daten ergeben die gleiche Datei
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(data)


def localized(parent: ET.Element, tag: str) -> dict:
    """{"C": englisch, "de": ...} aus <tag> und <tag xml:lang="de">."""
    return {el.get(LANG, "C"): " ".join(el.text.split()) for el in parent.findall(tag)}


def description(desc: ET.Element) -> dict:
    """Beschreibung je Sprache als das kleine HTML, das DEP-11 erwartet."""
    out: dict = {}
    for el in desc:
        if el.tag == "p":
            lang = el.get(LANG, "C")
            out[lang] = out.get(lang, "") + f"<p>{' '.join(el.text.split())}</p>"
        elif el.tag in ("ul", "ol"):
            items: dict = {}
            for li in el.findall("li"):
                items.setdefault(li.get(LANG, "C"), []).append(" ".join(li.text.split()))
            for lang, texts in items.items():
                lis = "".join(f"<li>{t}</li>" for t in texts)
                out[lang] = out.get(lang, "") + f"<{el.tag}>{lis}</{el.tag}>"
    return out


def q(value: str) -> str:
    """YAML-Text in doppelten Anführungszeichen (JSON-Strings sind gültiges YAML)."""
    return json.dumps(value, ensure_ascii=False)


def yaml_map(key: str, values: dict, indent: str = "") -> list:
    return [f"{indent}{key}:"] + [f"{indent}  {lang}: {q(text)}" for lang, text in values.items()]


def dep11(package: str, version: str) -> str:
    root = ET.parse(METAINFO).getroot()
    cid = root.findtext("id")
    lines = [
        "---",
        "File: DEP-11",
        "Version: '1.0'",
        f"Origin: {NAME}",
        f"MediaBaseUrl: https://epiklal.github.io/{NAME}",
        "---",
        f"Type: {root.get('type')}",
        f"ID: {cid}",
        f"Package: {package}",
        f"ProjectLicense: {root.findtext('project_license')}",
        *yaml_map("Name", localized(root, "name")),
        *yaml_map("Summary", localized(root, "summary")),
        *yaml_map("Description", description(root.find("description"))),
        "Developer:",
        f"  id: {root.find('developer').get('id')}",
        "  name:",
        f"    C: {q(root.findtext('developer/name'))}",
        "Categories:",
        *[f"- {c.text}" for c in root.findall("categories/category")],
        "Keywords:",
    ]
    keywords: dict = {}
    for kw in root.findall("keywords/keyword"):
        keywords.setdefault(kw.get(LANG, "C"), []).append(kw.text)
    for lang, words in keywords.items():
        lines.append(f"  {lang}:")
        lines += [f"  - {q(w)}" for w in words]
    lines += ["Url:"] + [f"  {u.get('type')}: {u.text}" for u in root.findall("url")]
    lines += [
        "Launchable:",
        "  desktop-id:",
        *[f"  - {l.text}" for l in root.findall("launchable")],
        "Provides:",
        "  binaries:",
        *[f"  - {b.text}" for b in root.findall("provides/binary")],
        "Icon:",
        f"  stock: {NAME}",
        "  cached:",
    ]
    for size in ICON_SIZES:
        lines += [f"  - name: {NAME}_{NAME}.png", f"    width: {size}", f"    height: {size}"]
    shots = root.findall("screenshots/screenshot")
    if shots:
        lines.append("Screenshots:")
        for shot in shots:
            lines.append(f"- default: {'true' if shot.get('type') == 'default' else 'false'}")
            if shot.findtext("caption"):
                lines += ["  caption:", f"    C: {q(shot.findtext('caption'))}"]
            lines += ["  source-image:", f"    url: {shot.findtext('image')}"]
    lines.append("Releases:")
    for rel in root.findall("releases/release"):
        lines += [f"- version: {q(rel.get('version'))}", "  type: stable", f"  unix-timestamp: {date_ts(rel.get('date'))}"]
    return "\n".join(lines) + "\n"


def date_ts(date: str) -> int:
    import datetime

    return int(datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.timezone.utc).timestamp())


def icon_tar(size: int) -> bytes:
    import shutil

    if shutil.which("rsvg-convert"):
        png = run("rsvg-convert", "-w", str(size), "-h", str(size), str(ICON_SVG))
    else:  # ImageMagick als Ersatz
        tool = "magick" if shutil.which("magick") else "convert"
        png = run(tool, "-background", "none", "-density", "600", str(ICON_SVG), "-resize", f"{size}x{size}", "png:-")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(f"{NAME}_{NAME}.png")
        info.size, info.mode = len(png), 0o644
        tar.addfile(info, io.BytesIO(png))
    return buf.getvalue()


def newest(apt: Path) -> tuple:
    """(Paketname, Version) des neuesten .deb im Pool."""
    best = None
    for deb in (apt / "pool").rglob("*.deb"):
        fields = dict(
            line.split(": ", 1)
            for line in run("dpkg-deb", "-f", str(deb), "Package", "Version").decode().splitlines()
        )
        key = (fields["Package"], fields["Version"])
        if best is None or subprocess.run(
            ["dpkg", "--compare-versions", key[1], "gt", best[1]]
        ).returncode == 0:
            best = key
    if best is None:
        sys.exit("Keine .deb-Dateien unter pool/")
    return best


def main() -> None:
    apt = Path(sys.argv[1]).resolve()
    dist = apt / "dists" / SUITE
    if dist.exists():
        subprocess.run(["rm", "-rf", str(dist)], check=True)
    packages = run("apt-ftparchive", "packages", "pool", cwd=apt)
    for arch in ARCHES:
        d = dist / COMPONENT / f"binary-{arch}"
        d.mkdir(parents=True)
        write_gz(d / "Packages.gz", packages)

    package, version = newest(apt)
    components = dep11(package, version).encode("utf-8")
    for arch in ARCHES:
        write_gz(dist / COMPONENT / "dep11" / f"Components-{arch}.yml.gz", components)
    for size in ICON_SIZES:
        write_gz(dist / COMPONENT / "dep11" / f"icons-{size}x{size}.tar.gz", icon_tar(size))

    release = run(
        "apt-ftparchive",
        "-o", f"APT::FTPArchive::Release::Origin={NAME}",
        "-o", f"APT::FTPArchive::Release::Label={NAME}",
        "-o", f"APT::FTPArchive::Release::Suite={SUITE}",
        "-o", f"APT::FTPArchive::Release::Codename={SUITE}",
        "-o", f"APT::FTPArchive::Release::Architectures={' '.join(ARCHES)}",
        "-o", f"APT::FTPArchive::Release::Components={COMPONENT}",
        "-o", "APT::FTPArchive::Release::Description=jellyfin-client-lite updates",
        "release", ".",
        cwd=dist,
    )
    (dist / "Release").write_bytes(release)
    run("gpg", "--batch", "--yes", "--clearsign", "-o", str(dist / "InRelease"), str(dist / "Release"))
    run("gpg", "--batch", "--yes", "-abs", "-o", str(dist / "Release.gpg"), str(dist / "Release"))
    print(f"Paketquelle für {package} {version} in {apt}")


if __name__ == "__main__":
    os.umask(0o022)
    main()
