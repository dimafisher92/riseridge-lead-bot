# Patched Google Maps scraper (Windows)

The official gosom/google-maps-scraper v1.18.1 Windows exe returns "unexpected page type" and 0 rows on this PC. Cause: gosom/scrapemate starts Chrome with `--single-process`, which crashes Chrome on Windows (upstream issue gosom/google-maps-scraper#322, open as of 2026-11-05). The fix is to drop that flag and build locally.

Built 2026-11-05 on this PC:

- Go 1.27.3 from https://go.dev/dl/ (zip, SHA-256 checked), unpacked to `C:/Users/dimaf/tools/go`.
- Sources in `C:/Users/dimaf/tools/gmaps-src`:

```bash
git clone --depth 1 --branch v1.18.1 https://github.com/gosom/google-maps-scraper.git
git clone --depth 1 --branch v1.4.0 https://github.com/gosom/scrapemate.git
sed -i '/--single-process/d' scrapemate/adapters/fetchers/jshttp/jshttp.go
cd google-maps-scraper
../../go/bin/go.exe mod edit -replace github.com/gosom/scrapemate=../scrapemate
../../go/bin/go.exe build -o ../../google-maps-scraper/gmaps-patched.exe .
```

- Output: `C:/Users/dimaf/tools/google-maps-scraper/gmaps-patched.exe`, set as `GMAPS_SCRAPER_EXE` in `.env`.
- First run downloads Playwright's Chrome (about 184 MB) to `%LOCALAPPDATA%/ms-playwright-go`.

The scraper's vendor skill (gosom's `google-maps-scraper` SKILL.md, references and Docker scripts) is kept as reference documentation in `docs/vendor/google-maps-scraper/`, not in `.claude/skills/`, so the nightly task never loads it. `skills-lock.json` at the repo root records where it was installed from.

To update: check that issue #322 is still open, re-clone at the new tags (match the scrapemate version in the scraper's `go.mod`), re-apply the two edits, rebuild, and run one small query before switching `.env`.
