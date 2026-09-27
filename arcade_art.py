#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daijishou Art Fetcher
=====================

Fill in missing box art for the *Daijishou* launcher (Android) from the public libretro
thumbnail repository, for both arcade and console systems, and wire it into Daijishou's DB.

Why
---
Daijishou's built-in scrapers often miss art: the libretro scraper matches arcade zips by
content CRC (which does not match), the arcade sources (Arcade Italia / DSESS) are often
unreachable, and some console titles never get matched by name. This tool fetches art
directly from libretro's ``thumbnails.libretro.com`` and writes it where Daijishou expects it.

Two matching modes
------------------
* ``rom``  — arcade: map the ROM short name (``mslug``) to a title via the libretro RDB
  databases, then to the ``Named_Boxarts`` filename.
* ``name`` — consoles: match the item's own name (No-Intro style) against the system's
  ``Named_Boxarts`` listing.

Output
------
* ``<out>/<itemId>/box_art.png`` + ``<out>/<itemId>/index.json`` for each fixed item.
* ``<out>/Daijishou.db`` — a copy of the input DB with ``preview_media_path`` filled in.

Standard library only.
"""

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RDB_BASE = "https://github.com/libretro/libretro-database/raw/master/rdb/"
THUMB_BASE = "https://thumbnails.libretro.com/"

# Daijishou platform unique_id -> (mode, rdb_file_or_None, thumbnails system folder)
PLATFORMS = {
    # arcade (match by ROM short name via libretro rdb)
    "fbneo":        ("rom",  "FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "neogeo":       ("rom",  "FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "mame":         ("rom",  "MAME.rdb", "MAME"),
    "mame2003plus": ("rom",  "MAME.rdb", "MAME"),
    "cps1":         ("rom",  "FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "cps2":         ("rom",  "FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "cps3":         ("rom",  "FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    # consoles (match by item name)
    "gb":           ("name", None, "Nintendo - Game Boy"),
    "gbc":          ("name", None, "Nintendo - Game Boy Color"),
    "gba":          ("name", None, "Nintendo - Game Boy Advance"),
    "nes":          ("name", None, "Nintendo - Nintendo Entertainment System"),
    "snes":         ("name", None, "Nintendo - Super Nintendo Entertainment System"),
    "n64":          ("name", None, "Nintendo - Nintendo 64"),
    "nds":          ("name", None, "Nintendo - Nintendo DS"),
    "psp":          ("name", None, "Sony - PlayStation Portable"),
    "psx":          ("name", None, "Sony - PlayStation"),
    "dreamcast":    ("name", None, "Sega - Dreamcast"),
    "megadrive":    ("name", None, "Sega - Mega Drive - Genesis"),
    "msx":          ("name", None, "Microsoft - MSX"),
}


def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "daijishou-art"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ---- libretro RDB (RARCHDB) parsing ----------------------------------------------------------

def _decode_value(buf, i):
    marker = buf[i]
    if marker == 0xD9:
        length = buf[i + 1]
        i += 2
    elif marker >= 0xA0:
        length = marker - 0xA0
        i += 1
    else:
        return None, i
    return buf[i:i + length].decode("latin1"), i + length


def parse_rdb(path):
    """Return {rom_name_lower: title}. Field order is name(,description),rom_name."""
    buf = open(path, "rb").read()
    mapping = {}
    i = 0
    while True:
        j = buf.find(b"\xa4name", i)
        if j < 0:
            break
        name, j2 = _decode_value(buf, j + 5)
        nxt = buf.find(b"\xa4name", j + 5)
        if nxt < 0:
            nxt = len(buf)
        rj = buf.find(b"\xa8rom_name", j2, min(j2 + 600, nxt))
        if name is not None and rj > 0:
            rom, _ = _decode_value(buf, rj + 9)
            if rom and name:
                mapping[rom.lower()] = name
        i = j + 5
    return mapping


# ---- libretro thumbnails listing -------------------------------------------------------------

def load_listing(folder):
    """{normalized(title): actual_png_filename} from a system's Named_Boxarts index."""
    page = http_get(THUMB_BASE + urllib.parse.quote(folder) + "/Named_Boxarts/").decode("latin1")
    names = [urllib.parse.unquote(html.unescape(n)) for n in re.findall(r'href="([^"]+\.png)"', page)]
    return {normalize(n[:-4]): n for n in names}


def rom_basename(uri):
    return urllib.parse.unquote(uri).split("/")[-1]


def fetch_boxart(url, dest):
    with open(dest, "wb") as f:
        f.write(http_get(url))


def main():
    ap = argparse.ArgumentParser(description="Fetch Daijishou box art from libretro thumbnails.")
    ap.add_argument("--db", required=True, help="path to a pulled Daijishou.db")
    ap.add_argument("--out", required=True, help="output folder (staging)")
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, "_cache")
    os.makedirs(cache, exist_ok=True)

    # 1. RDB maps (arcade) and boxart listings (all systems used)
    maps, listings = {}, {}
    for mode, rdb, folder in PLATFORMS.values():
        if mode == "rom" and rdb not in maps:
            p = os.path.join(cache, rdb)
            if not os.path.exists(p):
                print("download rdb:", rdb)
                open(p, "wb").write(http_get(RDB_BASE + urllib.parse.quote(rdb)))
            maps[rdb] = parse_rdb(p)
            print(f"  {rdb}: {len(maps[rdb])} titles")
    for folder in sorted({f for _, _, f in PLATFORMS.values()}):
        if folder not in listings:
            print("download listing:", folder)
            try:
                listings[folder] = load_listing(folder)
            except Exception as e:
                print("  skip", folder, e)
                listings[folder] = {}

    # 2. work the DB
    out_db = os.path.join(args.out, "Daijishou.db")
    shutil.copyfile(args.db, out_db)
    con = sqlite3.connect(out_db)
    cur = con.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    plat_ids = {r[1]: r[0] for r in cur.execute("select id, unique_id from PlatformEntity")}

    tasks = []  # (item_id, url, png)
    for uid, (mode, rdb, folder) in PLATFORMS.items():
        pid = plat_ids.get(uid)
        if pid is None or not listings.get(folder):
            continue
        for iid, name, uri in cur.execute(
                "select id, name, uri from PlayableItemEntity "
                "where attached_platform_id=? and (preview_media_path is null or preview_media_path='')",
                (pid,)):
            title = None
            if mode == "rom" and uri:
                rom = rom_basename(uri).lower()
                title = maps[rdb].get(rom) or maps[rdb].get(rom.rsplit(".", 1)[0] + ".zip")
            elif mode == "name":
                title = name
            fn = listings[folder].get(normalize(title)) if title else None
            if not fn and uri:
                fn = listings[folder].get(normalize(rom_basename(uri).rsplit(".", 1)[0]))
            if not fn:
                continue
            url = THUMB_BASE + urllib.parse.quote(folder) + "/Named_Boxarts/" + urllib.parse.quote(fn)
            d = os.path.join(args.out, str(iid))
            os.makedirs(d, exist_ok=True)
            tasks.append((iid, url, os.path.join(d, "box_art.png")))

    print(f"items to fetch: {len(tasks)}")

    def worker(t):
        iid, url, png = t
        if os.path.exists(png):
            return iid, True
        try:
            fetch_boxart(url, png)
            return iid, True
        except Exception:
            return iid, False

    ok = 0
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        for _, good in ex.map(worker, tasks):
            if good:
                ok += 1

    fetched = 0
    for iid, url, png in tasks:
        if not os.path.exists(png):
            continue
        with open(os.path.join(os.path.dirname(png), "index.json"), "w", encoding="utf-8") as f:
            json.dump({"boxArtPath": "box_art.png", "snapshotPath": None, "titlePath": None}, f)
        cur.execute("update PlayableItemEntity set preview_media_path=? where id=?",
                    (f"/data/user/0/com.magneticchen.daijishou/files/preview_media/{iid}/", iid))
        fetched += 1

    con.commit()
    cur.execute("PRAGMA journal_mode=DELETE")
    con.commit()
    con.close()

    print(f"downloaded {ok}/{len(tasks)}, DB updated for {fetched}")
    print("Output:", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
