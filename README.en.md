# Daijishou Arcade Art

A script that fills in missing **arcade box art** for the **Daijishou** launcher (Android)
using the public **libretro thumbnails** repository.

> Русская версия: [README.md](README.md)

## Why

Daijishou often fails to fetch art for arcade sets (FinalBurn Neo, MAME, Neo Geo): the
built-in libretro scraper matches by content CRC and arcade zips don't match, while the
arcade scrapers (Arcade Italia / DSESS) are frequently unreachable. Consoles
(NES/GBA/PS1…) end up with art, arcades stay blank.

This tool maps each arcade ROM short name (`mslug`) to its full title from the **libretro
RDB** databases, downloads the matching `Named_Boxarts` image and writes it into Daijishou's
database.

## Requirements

- Python 3 (standard library only).
- Root/adb access to the handheld (to pull and push Daijishou's database).

## Usage

### 1. Pull Daijishou's database

```bash
adb root
adb shell su -c "cp /data/data/com.magneticchen.daijishou/databases/Daijishou.db /sdcard/Download/"
adb pull /sdcard/Download/Daijishou.db
```

### 2. Run the script

```bash
python arcade_art.py --db Daijishou.db --out art
```

`art/` will contain:
- `art/<itemId>/box_art.png` + `art/<itemId>/index.json` for each fixed game;
- `art/Daijishou.db` — a copy of the DB with `preview_media_path` filled in.

### 3. Push it back

Stop Daijishou, unpack the art into its `preview_media`, replace the DB, start it again:

```bash
adb shell am force-stop com.magneticchen.daijishou

# artwork
cd art && tar -cf ../art.tar . && cd ..
adb push art.tar /sdcard/Download/art.tar
adb shell su -c "tar -xf /sdcard/Download/art.tar -C /data/data/com.magneticchen.daijishou/files/preview_media/"
adb shell su -c "chown -R u0_a116:u0_a116 /data/data/com.magneticchen.daijishou/files/preview_media/"

# database
adb push art/Daijishou.db /sdcard/Download/Daijishou_new.db
adb shell su -c "cp /sdcard/Download/Daijishou_new.db /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "rm -f /data/data/com.magneticchen.daijishou/databases/Daijishou.db-wal /data/data/com.magneticchen.daijishou/databases/Daijishou.db-shm"
adb shell su -c "chown u0_a116:u0_a116 /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "chmod 660 /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "restorecon /data/data/com.magneticchen.daijishou/databases/Daijishou.db"

adb shell monkey -p com.magneticchen.daijishou 1
```

> `u0_a116` is Daijishou's UID on that device; check yours:
> `adb shell su -c "ls -l /data/data/com.magneticchen.daijishou/databases/"`.

## How it works

- downloads `*.rdb` from `libretro/libretro-database` and parses the **RARCHDB** format
  (`name` + `rom_name` fields);
- downloads the per-system `Named_Boxarts` listing and builds a normalized title map;
- for each arcade item without art: title → filename → download the image;
- handles Daijishou platforms: `fbneo`, `neogeo`, `mame`, `mame2003plus`.

## License

MIT.
