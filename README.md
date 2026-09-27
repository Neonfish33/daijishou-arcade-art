# Daijishou Arcade Art

Скрипт, который добивает **обложки аркад** в лаунчере **Daijishou** (Android) из публичного
репозитория обложек **libretro thumbnails**.

> English version: [README.en.md](README.en.md)

## Зачем

Daijishou часто не может подтянуть обложки для аркадных сетов (FinalBurn Neo, MAME,
Neo Geo): встроенный libretro-скрапер матчит по CRC содержимого и аркадные zip не
совпадают, а аркадные источники (Arcade Italia / DSESS) часто недоступны. В итоге
аркады остаются без картинок, тогда как консоли (NES/GBA/PS1…) — с ними.

Скрипт сопоставляет короткое имя рома (`mslug`) с полным названием из баз **libretro RDB**,
скачивает соответствующий `Named_Boxarts` и прописывает его в базу Daijishou.

## Требования

- Python 3 (только стандартная библиотека).
- Root/adb-доступ к приставке (чтобы забрать и вернуть базу Daijishou).

## Использование

### 1. Забрать базу Daijishou с приставки

```bash
adb root
adb shell su -c "cp /data/data/com.magneticchen.daijishou/databases/Daijishou.db /sdcard/Download/"
adb pull /sdcard/Download/Daijishou.db
```

### 2. Запустить скрипт

```bash
python arcade_art.py --db Daijishou.db --out art
```

В `art/` появятся:
- `art/<itemId>/box_art.png` + `art/<itemId>/index.json` для каждой исправленной игры;
- `art/Daijishou.db` — копия базы с заполненным `preview_media_path`.

### 3. Вернуть на приставку

Останови Daijishou, распакуй обложки в его `preview_media`, подмени базу и запусти снова:

```bash
adb shell am force-stop com.magneticchen.daijishou

# обложки
cd art && tar -cf ../art.tar . && cd ..
adb push art.tar /sdcard/Download/art.tar
adb shell su -c "tar -xf /sdcard/Download/art.tar -C /data/data/com.magneticchen.daijishou/files/preview_media/"
adb shell su -c "chown -R u0_a116:u0_a116 /data/data/com.magneticchen.daijishou/files/preview_media/"

# база
adb push art/Daijishou.db /sdcard/Download/Daijishou_new.db
adb shell su -c "cp /sdcard/Download/Daijishou_new.db /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "rm -f /data/data/com.magneticchen.daijishou/databases/Daijishou.db-wal /data/data/com.magneticchen.daijishou/databases/Daijishou.db-shm"
adb shell su -c "chown u0_a116:u0_a116 /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "chmod 660 /data/data/com.magneticchen.daijishou/databases/Daijishou.db"
adb shell su -c "restorecon /data/data/com.magneticchen.daijishou/databases/Daijishou.db"

adb shell monkey -p com.magneticchen.daijishou 1
```

> UID `u0_a116` — это Daijishou у конкретного устройства; проверь свой:
> `adb shell su -c "ls -l /data/data/com.magneticchen.daijishou/databases/"`.

## Как это работает

- качает `*.rdb` из `libretro/libretro-database` и парсит формат **RARCHDB** (поля
  `name` + `rom_name`);
- качает листинг `Named_Boxarts` по системам и строит нормализованное сопоставление названий;
- для каждой аркадной игры без обложки находит название → файл → скачивает картинку;
- готово для платформ Daijishou: `fbneo`, `neogeo`, `mame`, `mame2003plus`.

## Лицензия

MIT.
