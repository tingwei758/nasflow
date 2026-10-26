# nasflow

nasflow watches qBittorrent for finished downloads, works out what each one is
(using its own parser and TMDB), and **hardlinks** it into a staging folder
already named the way Plex expects:

```
staging/Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv
staging/TV Shows/黑镜 (2011)/Season 6/黑镜 - S06E01.mkv
```

When nasflow isn't sure, it stops and asks you on a board instead of guessing.
You then move finished folders into your library yourself, in File Station.

Hardlinks take no extra space and appear instantly. qBittorrent keeps seeding
the originals, which nasflow never renames, moves, or modifies.

## The board

Open `http://<NASFLOW_LAN_IP>:<NASFLOW_PORT>` (e.g. `http://192.168.4.126:9987`).

| Column | Meaning |
|---|---|
| Downloaded | qBittorrent finished it; nasflow hasn't looked yet |
| Identifying | nasflow is looking it up |
| Needs you | nasflow stopped. Click **Choose…** to see why, pick the right match (or search), check the exact paths it will create, then **Confirm & link**, or mark it **Not media** |
| Ready to move | linked into staging, waiting for you to move it |

A card leaves the board once its folder is gone from staging, so there is
nothing to mark as done.

Some things to expect:

- **The board starts empty.** The first time nasflow reaches qBittorrent, it
  records everything already finished as done and leaves it alone. Only
  downloads that finish after that are filed. To file the existing ones too, set
  `skip_existing_on_first_run = false` before the first start.
- **A card stops once and waits.** When nasflow stops because it isn't sure, it
  doesn't ask TMDB again until you act. It *does* retry stops that can clear
  on their own: TMDB being unreachable, or a destination already being taken.
- **A second copy of something already in staging stops** with a destination
  conflict, because nasflow never overwrites. A 4K re-download is one example.
  Move the first copy to the library and the second one links on the next
  poll.
- **Seasons from separate downloads share a show folder.** If Season 1 and
  Season 2 are both in staging, both cards stay in Ready until the show
  folder is gone. Move each `Season N` folder, or the whole show folder.
- **Films or extras inside a TV pack are not filed as episodes.** A series
  pack that also contains the films is one example. The card stops and names
  them; confirming links the episodes only.
- **Startup refuses a broken setup** instead of silently failing every link.
  It checks that downloads and staging don't overlap, and makes one test
  hardlink (`staging/.nasflow-link-probe`), which it removes straight away.

## The one manual step: move into the library with File Station

Move finished folders from `docker/qb/staging/Movies` and
`docker/qb/staging/TV Shows` into `Media/Movies` and `Media/TV Shows` **using
File Station**.

> ⚠️ **Don't use `mv` in a shell.** `docker` and `Media` are separate shared
> folders, so a shell `mv` silently does a full multi-gigabyte **copy** and
> then deletes the original. File Station's move between shares on the same
> volume is a btrfs clone: instant, and it takes no extra space.

When a show's folder already exists in the library (a new season, say), move
the `Season N` folder rather than the show folder, so nothing already in the
library is up for replacement.

## Install (Synology Container Manager)

1. **Copy this repository** to the NAS, e.g. `/volume1/docker/nasflow-src`.
2. **Create the config folder** and the staging folder:
   - `/volume1/docker/nasflow`, for `config.toml` and `nasflow.db`
   - `/volume1/docker/qb/staging`

   Both must be writable by UID/GID `911`, the owner of qBittorrent's files.
3. **Create `.env`** from `.env.example` in the repository folder. Check
   `NASFLOW_LAN_IP` and `NASFLOW_PORT`.
4. **Create `/volume1/docker/nasflow/config.toml`** from `config.example.toml`. Fill in:
   - `qb_password`: the qBittorrent WebUI password
   - `tmdb_api_key`: the same key nastool uses (`rmt_tmdbkey` under `app:` in
     `/volume1/docker/nastools/config/config.yaml`). nasflow keeps its own copy and
     does not depend on nastool at runtime.

   Alternatively, leave them out of the file and set `NASFLOW_QB_PASSWORD` /
   `NASFLOW_TMDB_API_KEY` as container environment variables.
5. **Container Manager → Project → Create**, point it at the repository folder
   (it picks up `docker-compose.yml`), and start it.

### Why one mount, and why it isn't read-only

Linux refuses to hardlink across mount points (`EXDEV`). That includes two
bind mounts of the *same* folder, and it includes a read-only mount on the
source side. So `docker-compose.yml` mounts `NASFLOW_QB_ROOT` (the folder holding
both `downloads/` and `staging/`) **once, read-write, at the same path** as on
the host.

That means the kernel doesn't stop nasflow from writing to downloads, so nasflow's
code does:

- the linker refuses any destination that resolves outside `staging_root`
  (including via `..` or symlinks);
- the test suite fails if any other module makes a filesystem write.

The media library is not mounted at all.

## Configuration

Everything (paths, folder and file name templates, file extensions,
thresholds) lives in `config.toml`. See `config.example.toml` for every option
with comments. Notable settings:

- `score_accept` / `score_gap`: how sure nasflow must be before linking on its own.
- `require_english_title`: Chinese-only release names (e.g. `龙门飞甲.2011…`)
  have nothing to cross-check the match against, so they stop for you by
  default.
- `[layout]`: the naming convention. The defaults match the existing library:
  `Season 1`, not `Season 01`; specials go in `Season 0`.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

`scripts/dryrun.py` shows what nasflow *would* do with a downloads folder,
without linking anything.
