# flow — design

_2026-10-18_

## Intent and why

Today, getting a finished torrent into Plex takes three manual hops and one unreliable trigger:

1. Click a button in hlink's web UI to hardlink the download into a staging folder.
2. Move that folder by hand into nastool's watch folder.
3. Nastool is *supposed* to notice, identify, and rename — but its watcher often misses, so open nastool's UI and press manual start.
4. Move the renamed output by hand into `/volume1/Media/Movies` or `/volume1/Media/TV Shows`.

The renaming itself is good and rarely wrong. The failures are the hops and the flaky trigger.

**flow replaces hlink and nastool with one Python container.** It watches qBittorrent, identifies finished downloads, and hardlinks them into a staging tree already named the way the Plex library expects. The human moves finished folders into the library and resolves anything flow is unsure about — confirming decisions rather than doing clerical work.

## Goals

- No manual file handling between torrent completion and a correctly-named, ready-to-move folder.
- Identification at least as good as nastool's on this library's actual content, which is predominantly Chinese PT-site releases.
- When flow is unsure, it stops and waits rather than guessing.
- A board UI showing where every item is, with uncertain items visibly stuck and resolvable in place.

**The point is removing the copying, not removing the human.** Full automation is explicitly not the target. Keeping a person in the loop for decisions and special cases is fine and desirable; making a person shuttle files between four folders is not.

## Non-goals

- Handling Transmission (`tr`). qBittorrent only.
- Backfilling the 252 items already in `/volume1/docker/qb/downloads`, or touching the existing 673 movies and 83 shows. Those are considered done.
- Performing the final move into `/volume1/Media`. The human does that in File Station, deliberately.
- Reimplementing hlink's dedupe, prune, caching, or tree-mirroring features. They solve problems flow does not have.
- Managing torrents: adding, removing, seeding policy, ratio. flow only reads.

## Anti-goals

These would make flow a failure even if it technically worked:

- **Filing something in the wrong place silently.** A wrong guess that lands in the library is worse than stopping. Low confidence must stop.
- **Touching the files qBittorrent is seeding.** Source files are read-only to flow, always.
- **Requiring the human to tell flow what they already did.** No "mark as complete" buttons.
- **Reintroducing a directory watcher as the trigger** (see Constraints).

## Constraints

These were established empirically on the NAS and are not negotiable by the builder.

### Hardlinks cannot cross Synology shares

Each DSM shared folder is a separate btrfs subvolume with its own inode namespace (`docker` is `subvolid=260`, `Media` is `subvolid=261`; `st_dev` 40 vs 41). Verified:

```
ln /volume1/docker/qb/downloads/x  /volume1/Media/x
  → Invalid cross-device link (EXDEV)
rename(2) across the same boundary → EXDEV
mv across the boundary → inode changes (real copy)
```

**Therefore the staging tree must live inside the `docker` share.** flow never writes to `/volume1/Media`.

### The final move is free, but only in File Station

DSM's File Station uses btrfs fast-clone (reflink) for copies and moves within a volume, including across shares. Verified across the existing library: of 1,426 same-size video pairs over 200 MB between `downloads` and `Media`, **1,425 share identical physical extents** (~4,870 GiB stored once under two names). Only one file, `穿靴子的猫 (2011)` / `Puss.in.Boots.2011` (32 GiB), is a true duplicate.

A shell `mv` or `cp` would be a real copy. The human's File Station step is free. This is documented so a builder does not "helpfully" automate the final move with `shutil.move`, which would silently start consuming hundreds of gigabytes.

### The filesystem cannot tell you a download is finished

`Downloads\AppendExtension` is off, so partial files carry no `.!qB` suffix. `Session\TempPathEnabled` was switched on during design, so new downloads now stage in `/downloads/incomplete/` and are moved out on completion — but qBittorrent moves **files one at a time**, so a season folder can appear holding 3 of 12 episodes. The filesystem also cannot express paused, errored, or still-moving states.

**Therefore the trigger is polling the qBittorrent WebUI API, not a directory watcher.** This is the same class of bug as nastool's missed detections.

## Paths and environment

| Thing | Path |
|---|---|
| qBittorrent downloads (host) | `/volume1/docker/qb/downloads` |
| qBittorrent downloads (in qb container) | `/downloads/` |
| qBittorrent incomplete | `/downloads/incomplete/` |
| flow staging (new) | `/volume1/docker/qb/staging` |
| Plex library (human moves here) | `/volume1/Media/Movies`, `/volume1/Media/TV Shows` |
| Existing empty `qb/hlinks` | unused; leave alone |

The qBittorrent API reports container-internal paths (`/downloads/...`). flow must translate these to host paths. Make the mapping configuration, not a hardcoded string replace.

## Naming convention

Established by the human's May 2026 library-wide rename, documented in `/volume1/Media/TV_Shows_rename_plan.md`. flow must match it exactly:

```
Movies/标题 (YYYY)/标题 (YYYY).ext
TV Shows/标题 (YYYY)/Season N/标题 - SxxEyy.ext
```

- Chinese title when TMDB has one; English otherwise (`Cunk on Earth (2022)`, `Jing (2018)`).
- Simplified Chinese, even where Plex displays traditional.
- Season folders are `Season 1`, not `Season 01`.
- Specials (`S03E00`) go in `Season 0`.

## How identification works

### Classification comes from the files, not the torrent name

This is the central design decision. Every TV episode file in this library carries `SxxExx` in its own filename, regardless of how messy the enclosing folder name is. Movie folders hold one large video with no episode markers.

```
if any video file inside matches SxxExx  → TV, and each file self-describes
elif exactly one significant video file  → movie
else                                      → stop, ask the human
```

This dissolves the cases that would otherwise be hardest:

- `黑镜S01-S05.Black.Mirror.2011-2019...` — never parse `S01-S05` or the year range. Each file says `Black.Mirror.S03E04`. Five seasons sort themselves; `S03E00.Special.White.Christmas` lands in `Season 0`.
- `长安的荔枝.The.Litchi.Road.S01.2025` — year-after-season confuses movie parsers; irrelevant here.
- `龙门飞甲.2011.HKG...` — no `SxxExx`, one video, so a movie. No ambiguity about type.

Ignore sample files, extras, and anything under a size floor when counting "significant" videos.

### Title extraction

Release names follow a bilingual convention: `中文名 . English.Title . [Sxx] . Year . quality . -GROUP`. Strip the Chinese prefix and what remains is near-standard and handled by an off-the-shelf parser (`guessit` is the starting point; the builder should evaluate it against the corpus below before committing).

Known hazards in this library, all real:

- `￡` as a separator, and CJK group names: `￡cXcY@FRDS`, `￡CMCT星辰`
- Bracketed Chinese prefixes containing spaces and parentheses: `[雪国列车(剧版) 第一季]`
- `第一季` duplicating `S01`
- Region tags that look like scene tags: `.CHN.`, `.HKG.`, `.EUR.`
- `Version.Director's.Cut`, `V2`, `60fps`, `6Audios`
- **No English title at all**: `龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰`

### TMDB lookup

- Search with the English title when present; fall back to the Chinese title with `language=zh-CN`.
- Constrain by year where the name supplies one.
- Fetch the Chinese title for the output path even when matching on English.
- Reuse the existing API key from nastool's `config.yaml` (do not print it into logs).

### When to stop

flow stops rather than guessing when:

- no TMDB match
- the top match is weak
- the top two matches are close
- the match is confident but the evidence is thin — e.g. a Chinese-only name with no English title to cross-check, which is why `龙门飞甲` stops even at 0.94
- classification is ambiguous (no `SxxExx` but several large videos)
- no video files at all (`CE教学实例应用-由浅入深学习游戏修改 [全十课]`, `Anjelica JPEG`)

**Thresholds are left to the builder** but must be configuration, not constants, and must be tuned against the corpus below. Err toward stopping: a stopped item costs a click, a wrong guess costs trust.

## The board

Four columns. Real states, not decoration:

| Column | Meaning |
|---|---|
| Downloaded | qBittorrent reports complete; flow hasn't processed it yet |
| Identifying | parsing and TMDB lookup in flight |
| Needs you | stopped; requires a human decision |
| Ready to move | hardlinked into staging, waiting for the human's File Station move |

**Cards leave on their own.** flow watches the staging tree; when a folder disappears because the human moved it, the card moves to Done. There is no "mark complete" control. (Watching staging for *disappearance* is safe — unlike watching downloads for *appearance*, which is the thing the Constraints section forbids.)

### Resolve panel

Opened from a card in "Needs you". Validated against a mockup. Contains:

- the raw release name
- what flow worked out alone: file count, size, classification and why (`no SxxExx → movie`), extracted title and year
- ranked TMDB candidates with poster, Chinese and English titles, year, director, a confidence score, and a one-line reason (`title exact · year exact`, `similar title · year mismatch`)
- a manual TMDB search box
- **a preview of the exact paths that will be created**, before committing
- actions: Confirm & link · Not media — ignore · Leave for later

A human decision should be remembered, so re-processing the same torrent doesn't ask twice.

## Duplicates and upgrades

**Decided with the human: flow does not check whether a title already exists.** It links into staging regardless.

The reasoning is that the final move is a human action in File Station, so a collision surfaces there as an ordinary file-exists prompt, with the human already looking at both versions and better placed to judge than any heuristic flow could run. Re-downloading a 4K version of an existing 1080p film therefore just works: it appears in staging, and the human decides at move time.

This means flow performs **no scanning of `/volume1/Media`** at all. It needs no read access to the library share.

This decision is contingent on the human performing the final move. If flow is ever extended to write into the library itself, replace-or-keep becomes a question it must ask before overwriting anything.

## Details settled during review

These were not discussed with the human; they are the author's calls and are flagged as such.

### Subtitles and companion files

Chinese PT releases frequently ship external subtitles (`.srt`, `.ass`, `.sub`, `.idx`), and the existing source folders contain `.nfo`, `cover.jpg`, and `_海报.jpg` files.

**Call:** hardlink external subtitle files alongside their video, renamed to match the video's new stem so Plex pairs them automatically (`标题 (YYYY).zh.srt`, `标题 - S01E01.ass`). Preserve any language suffix already present. Do **not** link `.nfo`, posters, or `简介.txt` — Plex fetches its own metadata, and stale `.nfo` files actively mislead it.

### Ongoing seasons arrive in pieces

A currently-airing show delivers episodes over weeks, and the human will have already moved earlier episodes out of staging. flow will therefore create a staging folder containing only the new episodes, in the same `标题 (YYYY)/Season N/` shape.

**Call:** this is correct and expected. The human merges folders in File Station, which is a normal merge-on-move. flow does not attempt to track what it previously handed over.

### TMDB key handling

**Call:** copy the key value into flow's own configuration rather than reading nastool's `config.yaml` at runtime. flow must not depend on another container's files, since the point is to retire nastool. Never log the key.

### Staging hygiene

**Call:** after a human move, prune empty directories left behind in staging (e.g. an emptied `Movies/标题 (YYYY)/`). Only ever remove empty directories, never files.

## Technology

Decided with the human:

- **Python** backend — chosen for the torrent-parsing and TMDB ecosystem, which is where the project's difficulty lives.
- **SQLite** for state — one file, no second container.
- **Web UI served by the same process** — one container total.
- Runs as a Docker container on the Synology, alongside the existing stack.
- Needs read access to `/volume1/docker/qb/downloads`, write access to `/volume1/docker/qb/staging`, and network access to the qBittorrent WebUI and TMDB. It does **not** need access to `/volume1/Media`.

### Exposure

**Decided with the human: no authentication.** The board binds to the LAN only and must not be reachable from the internet. The NAS answers on a public hostname, so the builder must ensure the container's port is not published beyond the local network and no reverse-proxy rule exposes it.

Frontend framework, web framework, parsing library, and schema are the builder's call.

## Test corpus

Identification must be evaluated against real names before the build is called done. These are from the NAS and cover the hard cases:

```
长安的荔枝.The.Litchi.Road.S01.2025.2160p.WEB-DL.H265.AAC-HHWEB
闪电侠.The.Flash.2023.BluRay.1080p.x265.10bit.DDP7.1.MNHD-FRDS
[雪国列车(剧版) 第一季].Snowpiercer.2020.S01.1080p.NF.WEB-DL.H264.DDP5.1-PTerWEB
[霍元甲]Fearless.Version.Director's.Cut.2006.CHN.Blu-ray.1080p.x264.DTS-HD.MA.7.1-BtsHD.mkv
[飞驰人生2].Pegasus.2.2024.2160p.60fps.V2.WEB-DL.HEVC.10bit.DTS5.1.6Audios-QHstudIo
飞驰人生.Pegasus.2019.BluRay.1080p.x265.10bit.MNHD-FRDS
[首尔之春].12.12.The.Day.2023.HKG.BluRay.1080p.x264.DDP5.1-CMCT
黑镜S01-S05.Black.Mirror.2011-2019.1080p.WEB-DL.x265.AC3￡cXcY@FRDS
龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰
12 Monkeys S01 EUR BluRay 1080p DTS-HD MA 5.1 x265.10bit-CHD
A Quiet Place.2018.UHD.BluRay.2160p.Atmos.TrueHD.7.1.HDR.x265.10bit-CHD
El.Camino.a.Breaking.Bad.Movie.2019.2160p.NF.WEB-DL.DDP5.1.Atmos.HDR.HEVC-HHWEB
Eight.Hours.Dont.Make.a.Day.1972.E01-E05.CC.BluRay.1080p.DTS-HD.MA.1.0.x265.10bit-BeiTai
CE教学实例应用-由浅入深学习游戏修改 [全十课]
```

Note `El.Camino.a.Breaking.Bad.Movie` is a **movie** whose name contains a show title, and `Eight.Hours.Dont.Make.a.Day...E01-E05` uses `Exx` without `Sxx`. Both are traps.

The full set of 252 download names and 756 library folder names is available on the NAS for broader evaluation.

## Open to the builder

- Web framework, frontend approach, SQLite schema, polling interval.
- Choice of parsing library and how much custom pre-processing sits in front of it. Evaluate before committing; the Chinese-prefix strip is expected to be custom.
- Exact confidence scoring and thresholds (must be configurable and corpus-tuned).
- How the staging-disappearance watch is implemented (poll vs inotify).
- Whether multi-season packs produce one card or one per season.
- Error handling, retries, and logging.
