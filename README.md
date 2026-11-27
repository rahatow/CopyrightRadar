# CopyrightRadar

**Musik-Abmahnungen vorbeugen:** prüft die Musik in Ihren Instagram Reels auf Urheberrechtsrisiken.

Self-hosted tool for a studio, salon, clinic or any small business: check the music in **your own** Instagram Reels for the risk of a copyright claim.

> **Not legal advice.** The tool shows risk indicators from Meta's copyright check and from audio recognition (ACRCloud). Whether to remove a post, and how to answer a claim, is a decision for you and your lawyer.

Status: **v0.1, early.** Tested with automated tests, a demo and, on 27.11.2026, a real test Instagram account, Meta app and ACRCloud key (see [docs/SETUP.md](docs/SETUP.md)). The spec is in [docs/SPEC.md](docs/SPEC.md) (Russian for now). License: [AGPL-3.0](LICENSE).

## What it does

- **Reads** your posts and Meta's `copyright_check_information` through the Instagram API (tokens from Meta's new "Unified login" via graph.facebook.com, or older Instagram Login tokens via graph.instagram.com). Read-only: GET requests only; nothing is posted, archived or deleted.
- **Imports your Instagram data export** (ZIP, JSON format). Meta does not let apps download videos with copyrighted music, so the export is the only way to analyse exactly the riskiest Reels. It also shows posts that are already in the Instagram archive. Works even without a Meta app.
- **Recognises the music** with **your own** ACRCloud key: up to three 15-second fragments per video (start, middle, end).
- **Rates each post**: high, medium, unknown, low, no music, no sound — with reasons. The rules are a config file. Meta's "no match" never lowers a risk level.
- **Instagram-like page**: grid with risk badges, filter chips, tabs "In feed" / "Archive" (photos inside the archive with a "Photo" flag), a post card with reasons, ACRCloud result, Meta history, audio player and the source of the audio.
- **Refresh** compares the feed with Meta: posts that disappeared move to "Archive" with the check date, posts that came back return. Nothing changes when Meta's answer is incomplete, when a post has no link, or when more than 25 % of the feed disappears at once — then you confirm the exact number.
- **Manual steps**: "Mark as archived" (for posts you archived in the app), review after listening (risk confirmed / OK / no music, with a note), upload a file for a single post.
- **CSV export** for your lawyer, with the disclaimer.
- **German (default), English, Russian.** Light and dark theme. Works on a phone.

Everything runs on your machine. You use your own Meta app and your own ACRCloud key. The project authors receive no data.

## Quick start

Requirements: Docker with the Compose plugin.

```bash
git clone <repo-url> copyright-radar && cd copyright-radar
docker compose up -d --build
```

Open http://127.0.0.1:8000 and follow the setup: password → Instagram token → ACRCloud key → Refresh → (optional) import your export. **Step-by-step guide with screenshots: [docs/SETUP.md](docs/SETUP.md).**

Try it without any accounts first (invented posts):

```bash
DEMO=1 docker compose up -d --build
```

Optional: set `APP_SECRET_KEY` (see `.env.example`); otherwise the app creates a key in `data/`. Change the risk rules: copy `config/risk_rules.example.toml` to `config/risk_rules.toml`, edit, restart. Another port: `PORT=8080 docker compose up -d`.

**Access from other devices:** the container listens on 127.0.0.1 only. Put a reverse proxy with HTTPS in front (Caddy, nginx, Traefik) and keep the app's password login on.

**Your data** stays in `./data`: the SQLite database, encrypted tokens, previews, downloaded and imported media. Back it up; delete it to remove everything.

## Run the tests

```bash
docker build --target test -t copyright-radar:test .
docker run --rm copyright-radar:test
```

## Known limits of Meta's data

- Meta does not return `media_url` for videos with copyrighted audio, so the riskiest Reels cannot be downloaded through the API. Import your Instagram data export (ZIP) instead.
- Archived and deleted posts look the same in the API, and there is no archive date. The tool records the date it first noticed a post missing.
- Meta's "no match" is a weak signal: Meta has not checked many older videos and misses some commercial tracks. The tool never lowers a risk level because of it.
- Audio fingerprinting misses many covers, remixes and music under voice. Listen to anything marked "not recognized".

## Layout

```
app/
  main.py      FastAPI: pages, JSON API, setup wizard, CSV export
  service.py   analysis pipeline, export import, background worker
  auth.py      password login, signed session, same-origin check
  demo.py      DEMO=1 data
  instagram/   read-only Graph API client, Refresh with guards
  recognition/ ACRCloud client
  audio/       ffmpeg / ffprobe helpers
  importer/    Instagram data export (ZIP) parser
  risk/        risk rules (pure functions)
  storage/     SQLite, encrypted secrets
  web/         templates, static JS/CSS, i18n (de, en, ru)
config/        risk_rules.example.toml
docs/SPEC.md   specification
tests/         pytest (no network: fake Meta and ACRCloud)
```

## Not verified yet

- Commercial tracks and Facebook Sound Collection tracks on a real account (needs Reels made in the Instagram mobile app).
- Archive detection on a real account (archiving is only possible in the mobile app).
- The export parser was written against the known JSON layout and synthetic files; check it with a fresh real export.
- Meta rate-limit error codes.
