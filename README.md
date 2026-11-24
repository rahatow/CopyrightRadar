# CopyrightRadar

**Musik-Abmahnungen vorbeugen:** prüft die Musik in Ihren Instagram Reels auf Urheberrechtsrisiken.

Self-hosted tool for a studio, salon, clinic or any small business: check the music in **your own** Instagram Reels for the risk of a copyright claim.

> **Not legal advice.** The tool shows risk indicators from Meta's copyright check and from audio recognition (ACRCloud). Whether to remove a post, and how to answer a claim, is a decision for you and your lawyer.

Status: **pre-alpha scaffold.** The spec is in [docs/SPEC.md](docs/SPEC.md) (Russian for now). License: [AGPL-3.0](LICENSE).

## What it does (v0.1 target)

- Reads your Reels list and Meta's `copyright_check_information` (read-only: GET requests only, nothing is posted, archived or deleted).
- Recognises the music with **your own** ACRCloud key (15-second fragments).
- Gives each Reel a risk level with reasons: high, medium, unknown, low, no music, no sound. Rules are a config file you can edit.
- Shows an Instagram-like grid with "In feed" and "Archive" tabs, a post card, manual review, and a CSV export.
- UI in German (default), English and Russian.

Everything runs on your machine. You use your own Meta app and your own ACRCloud key. The project authors receive no data.

## Quick start

Requirements: Docker with the Compose plugin.

```bash
git clone <repo-url> copyright-radar && cd copyright-radar
docker compose up -d --build
```

Optional: set `APP_SECRET_KEY` (see `.env.example`); otherwise the app creates a key in `data/`. To change the risk rules, copy `config/risk_rules.example.toml` to `config/risk_rules.toml` and edit it.

Open http://127.0.0.1:8000/health. The setup wizard (Meta app, Instagram token, ACRCloud key) comes in v0.1; see SPEC section 11.

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
  instagram/   read-only Graph API client, sync guards
  recognition/ ACRCloud client
  audio/       ffmpeg fragments
  importer/    Instagram data export import
  risk/        risk rules (pure functions)
  storage/     SQLite, encrypted secrets
  setup/       setup wizard
  web/         UI, i18n (de, en, ru)
config/        risk_rules.example.toml
docs/SPEC.md   specification
tests/
```

## License

Copyright (C) 2026 CopyrightRadar contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License, version 3 only (AGPL-3.0-only). If you run a modified version as a network service, you must offer its source code to the users of that service. See [LICENSE](LICENSE).
