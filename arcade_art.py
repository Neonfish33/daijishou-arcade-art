#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daijishou Arcade Art
====================

Fetch missing arcade box art for the *Daijishou* launcher (Android) straight from the
public libretro thumbnail repository, and wire it into Daijishou's database.

Why this exists
---------------
Daijishou's own scraper often fails to fetch art for arcade sets (FBNeo / MAME / Neo Geo),
because the libretro scraper matches by content CRC and arcade zips don't match, while the
built-in arcade scrapers (Arcade Italia / DSESS) are frequently unreachable. This tool maps
each arcade ROM (short name, e.g. ``mslug``) to its libretro title via the libretro RDB
databases, downloads the matching ``Named_Boxarts`` image, and writes it where Daijishou
expects it.

Output
------
* ``<out>/<itemId>/box_art.png`` and ``<out>/<itemId>/index.json`` for every item it fixed.
* ``<out>/Daijishou.db`` — a copy of the input DB with ``preview_media_path`` filled in.

Then copy the per-item folders into Daijishou's ``files/preview_media/`` and replace its DB
(see README).

Requires only the Python standard library.
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

# Daijishou platform unique_id -> (rdb file, thumbnails system folder)
PLATFORMS = {
    "fbneo": ("FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "neogeo": ("FBNeo - Arcade Games.rdb", "FBNeo - Arcade Games"),
    "mame": ("MAME.rdb", "MAME"),
    "mame2003plus": ("MAME.rdb", "MAME"),
}


def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "daijishou-arcade-art"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ---- libretro RDB (RARCHDB) parsing ----------------------------------------------------------

def _decode_value(buf, i):
    """RARCHDB stores strings length-prefixed; lengths >= 0x20 use a 0xD9 marker + 1-byte len."""
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
    """{normalized(title): actual_png_filename} from the Named_Boxarts directory index."""
    page = http_get(THUMB_BASE + urllib.parse.quote(folder) + "/Named_Boxarts/").decode("latin1")
    names = [urllib.parse.unquote(html.unescape(n)) for n in re.findall(r'href="([^"]+\.png)"', page)]
    return {normalize(n[:-4]): n for n in names}


def rom_name_from_uri(uri):
    return urllib.parse.unquote(uri).split("/")[-1].lower()


def fetch_boxart(url, dest):
    data = http_get(url)
    with open(dest, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser(description="Fetch arcade box art for Daijishou from libretro thumbnails.")
    ap.add_argument("--db", required=True, help="path to a pulled Daijishou.db")
    ap.add_argument("--out", required=True, help="output folder (staging)")
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, "_cache")
    os.makedirs(cache, exist_ok=True)

    # 1. RDB maps
    maps, listings = {}, {}
    for rdb, folder in set(PLATFORMS.values()):
        p = os.path.join(cache, rdb)
        if not os.path.exists(p):
            print("download rdb:", rdb)
            open(p, "wb").write(http_get(RDB_BASE + urllib.parse.quote(rdb)))
        maps[rdb] = parse_rdb(p)
        if folder not in listings:
            print("download listing:", folder)
            listings[folder] = load_listing(folder)
        print(f"  {rdb}: {len(maps[rdb])} titles")

    # 2. work the Daijishou DB
    out_db = os.path.join(args.out, "Daijishou.db")
    shutil.copyfile(args.db, out_db)
    con = sqlite3.connect(out_db)
    cur = con.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    plat_ids = {r[1]: r[0] for r in cur.execute("select id, unique_id from PlatformEntity")}

    tasks = []  # (item_id, url, png_path)
    for uid, (rdb, folder) in PLATFORMS.items():
        pid = plat_ids.get(uid)
        if pid is None:
            continue
        for iid, uri in cur.execute(
                "select id, uri from PlayableItemEntity "
                "where attached_platform_id=? and (preview_media_path is null or preview_media_path='')",
                (pid,)):
            if not uri:
                continue
            rom = rom_name_from_uri(uri)
            title = maps[rdb].get(rom) or maps[rdb].get(rom.rsplit(".", 1)[0] + ".zip")
            fn = listings[folder].get(normalize(title)) if title else None
            if not fn:
                continue
            url = (THUMB_BASE + urllib.parse.quote(folder) + "/Named_Boxarts/"
                   + urllib.parse.quote(fn))
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
