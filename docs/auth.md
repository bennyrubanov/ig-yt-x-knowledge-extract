# Auth — no Instagram (or X) OAuth

There is **no** Instagram app grant, Graph API, “Connect Instagram,” or official download API in this repo. Same for X. The download scripts reuse a **browser session** you already have.

Agents: if the jar is missing, **stop** and give the human this page. Do not invent OAuth. Do not log into Google or Instagram as the agent. Do not commit, log, echo, or paste cookie files.

Cloud / Codespace / a VM without the operator’s Chrome **cannot** download Instagram. Ask them to run setup on a machine that is logged in.

## What the scripts actually read

| Site | File | Required? |
|------|------|-----------|
| Instagram reels + carousels | `~/.config/ig-cookies.txt` (`%USERPROFILE%\.config\ig-cookies.txt` on Windows) | **Yes.** `python scripts/igx.py reel` / `carousel` exit if it is missing. |
| X / Twitter video | `~/.config/x-cookies.txt` | Optional. Thread text still comes from FixTweet. |

Netscape (Mozilla) format. First line `# Netscape HTTP Cookie File` or `# HTTP Cookie File`. On Unix, `chmod 600`. Instagram needs the HttpOnly **`sessionid`** row. An export that skips HttpOnly will 403 / empty-media while the post is still live.

`python3 scripts/check-setup.py` only checks that the **row exists**. It does **not** prove Instagram will serve media. A stale `sessionid` from a logged-out Chrome session is still a green check. Log into [instagram.com](https://www.instagram.com/) in the browser first, then re-export, then probe one reel (see below).

An expired **exported cookie file** does not prove the browser is logged out. First check the browser the operator actually uses: if the requested reel plays while logged in, another login is unnecessary. A Codex in-app browser session is separate from Chrome and is not automatically available to `yt-dlp --cookies-from-browser chrome:Default`; do not export Chrome merely because a different browser works. Use supported browser media export when available, or the documented attended export from the browser/profile that holds the session. A successful public download despite a stale-cookie warning is not evidence that the jar has been repaired. Network/DNS failures are also not evidence of expired authentication.

Extract copies that file to a throwaway jar and passes **the copy** to `yt-dlp --cookies`. yt-dlp always writes the file back; after a failed Instagram fetch that write-back can delete the `sessionid` row. The live export must stay untouched. Hand-rolled `yt-dlp` probes must use a copy too. `python scripts/igx.py` never dumps Chrome itself. On this Mac, the **agent** writes the jar with **one** attended `--cookies-from-browser` (option B). The operator clicks Keychain **Allow**. Do not ask them to use a cookie-extension export unless they prefer that.

## Write the Instagram jar (pick one)

This operator’s usual path is **B** (agent dump, Keychain click). **A** is optional if they already use a cookie-extension.

### A — Instagram-only export (extension; optional)

1. In Chrome (or Firefox), log into [instagram.com](https://www.instagram.com/) as the account that can see the posts.
2. On an instagram.com tab, export **Netscape cookies**, **including HttpOnly**. A common tool is the “Get cookies.txt LOCALLY” extension. Cookie-Editor and similar work if they actually write HttpOnly `sessionid`.
3. Save the file as `~/.config/ig-cookies.txt` (Windows: `%USERPROFILE%\.config\ig-cookies.txt`):

```bash
mkdir -p ~/.config
# move/copy the export onto:
#   ~/.config/ig-cookies.txt
chmod 600 ~/.config/ig-cookies.txt   # Unix; skip on Windows
```

Then check **without printing values**:

```bash
python3 scripts/check-setup.py
```

### B — Dump from Chrome via yt-dlp (attended; this operator)

This is what agents run on this Mac. It writes **every site’s** cookies from that Chrome profile; filter to Instagram before keeping the file, and delete the full dump. Do not tell the operator to “export cookies” with an extension unless they ask.

On macOS the Keychain prompt must be **Allow**, not Always Allow. A human has to click it. **One dump.** Each `--cookies-from-browser` can prompt Keychain **twice** in one command (`--cookies` + browser extract). Retrying Default, then Profile 1, then a live probe is how you get 6–10 dialogs. Close extra Chrome instances if yt-dlp says the cookie DB is locked.

`--cookies FILE` is also a **write-back**. If the file is empty, yt-dlp errors (`does not look like a Netscape format cookies file`). Seed a header first. After a failed Instagram fetch, write-back can **strip `sessionid`**. `python scripts/igx.py` already copies the jar. A manual probe must still use a **copy**, never `~/.config/ig-cookies.txt`.

`chrome:Default` is Chrome’s first on-disk profile (`…/Google/Chrome/Default`). A second Chrome person is `Profile 1`. Export from the profile that is actually logged into Instagram.

```bash
mkdir -p ~/.config /tmp
# Seed a Netscape header so --cookies will accept the file.
printf '%s\n' '# Netscape HTTP Cookie File' > /tmp/all-cookies.txt
chmod 600 /tmp/all-cookies.txt
# Full-profile dump (secret). Do not commit or paste. One run.
yt-dlp --cookies-from-browser chrome:Default \
  --cookies /tmp/all-cookies.txt \
  --skip-download --simulate "https://example.com/" || true
# Keep Instagram rows only:
awk 'BEGIN{print "# Netscape HTTP Cookie File"}
     $0 ~ /^# Netscape/ || $0 ~ /^# HTTP Cookie/ {next}
     $1 ~ /instagram\.com/ || $1 ~ /#HttpOnly_.*instagram\.com/ {print}' \
     /tmp/all-cookies.txt > ~/.config/ig-cookies.txt
chmod 600 ~/.config/ig-cookies.txt
rm -f /tmp/all-cookies.txt
python3 scripts/check-setup.py
# Prove media, without clobbering the live jar:
cp ~/.config/ig-cookies.txt /tmp/ig-cookies-probe.txt
chmod 600 /tmp/ig-cookies-probe.txt
yt-dlp --cookies /tmp/ig-cookies-probe.txt --skip-download \
  --print "%(id)s | %(duration)s" "https://www.instagram.com/reel/REEL_ID/"
rm -f /tmp/ig-cookies-probe.txt
```

Use `chrome:"Profile 1"` (or another named profile) if Instagram is not in Default. Do not dump a second profile “just to check” — that is more Keychain prompts. If the probe still returns empty media, the browser is logged out or that post is gone. Do not dump again until the human confirms a reel plays while logged in.

**One-off download** without writing a jar (human at Keychain):

```bash
yt-dlp --cookies-from-browser chrome:Default "<REEL_URL>"
```

The Python CLI (`scripts/igx.py`) still will not run Instagram downloads until `~/.config/ig-cookies.txt` exists. This one-off is only for debugging yt-dlp.

## X (optional)

Same Netscape recipe → `~/.config/x-cookies.txt`. Only needed when yt-dlp hits a login wall or age-gate on video. Do not use the official X API.

## Login paused / “try again later”

Instagram is pausing **this login**, not proving a permanent ban and not saying the password is wrong. The usual trigger is the same logged-in browser hitting media endpoints in a burst (this pipeline reuses the Chrome session via cookies; there is no OAuth grant).

**2026-08-23:** a Notion inbox batch of 19 Instagram URLs in about 20 minutes, then two same-run retries on posts that already came back empty. Seventeen items had already downloaded, so the session was valid at the start. After the run, `python3 scripts/check-setup.py` reported **no `sessionid` row** — yt-dlp had been pointed at the live jar and write-back deleted it. Instagram separately decided the session looked automated and paused Chrome login.

Copying the jar (now default) stops the **local** wipe. It does not stop Instagram from checkpointing a burst. Spacing and no same-run retry are what reduce that.

While the timer is up:

1. Wait. Do not keep submitting the login form; that extends it.
2. Do not re-export cookies until a reel **plays in Chrome while logged in**. Then one HttpOnly export into `~/.config/ig-cookies.txt` ([recipe above](#write-the-instagram-jar-pick-one)).
3. Do not run this pipeline against Instagram until the human says browser login works.
4. Empty-media **or HTTP 400** Instagram URLs: mark `fail` and stop. Do **not** retry them in the same burst. A spaced 8-ok then 400 (2026-08-26) is still a stop.
5. Batches default to `--workers 1` and `--ig-gap 45`. Do not raise workers to finish faster. After a login pause, use **longer, uneven** waits between Instagram jobs (about **90–180 s**, picked at random). Split a large inbox across sittings; **resume by skipping jsonl `exit==0` URLs** — do not restart the prefix. 2026-08-27: 32, pause, then 29 more (18 leftover reels + 11 carousels) all served. Empty-media / HTTP 400 still fail-once. Random User-Agents, proxies, or extra `yt-dlp` probes are not part of this pipeline — they look more automated, not less.

`--workers 1`, no cookie paste, and not looping `--cookies-from-browser` were already correct. The volume plus failed retries is what looked bot-like.

## Agents must not

- Ask the user to “connect Instagram” in Cursor / Claude / Codex
- Put cookies in the repo, chat, jsonl, Notion, or vault
- Crawl Chrome or the cookie jar to list Saved collections (use the official Accounts Center ZIP — see `AGENTS.md`)
- Re-export on a cloud agent and hope the operator’s session appears
- Pass `~/.config/ig-cookies.txt` straight to `yt-dlp --cookies` (the CLI copies it; a manual probe must copy it too)
- Retry empty-media Instagram URLs in the same run
- Hammer the Instagram login form, or re-export, while Chrome shows a pause / checkpoint
- Hit Instagram from this repo until the human confirms a reel plays logged-in

Re-export only after the browser session works again. Sessions expire; a checkpoint is not “export harder.”

## Automated-activity warning without a download failure

A user-reported automated-activity notice is a stop-and-review signal even when
normal playback still works and every download succeeded. Successful responses
only establish retrieval, not account safety or permission to continue. Detection
may become visible at a later login; its exact trigger cannot be inferred from a
successful extraction log.

Stop account-facing Instagram collection and review the capture approach before
resuming a bulk run. Prefer cached media, user-supplied files/recordings or the
original linked source. Keep transcription, analysis and filing concurrency local.
Do not treat a fresh process, cookie refresh or another browser as resetting the
warning. Browser-driven collection is still automated activity.

Spacing and smaller batches reduce request volume; no interval or daily count here
is a verified safe allowance. Do not describe the default gap, or a longer gap, as
preventing account warnings. Avoid unnecessary metadata probes and re-downloads;
never rotate accounts, proxies or browser identities to get around the notice.
These are agent operating instructions, not a new runtime enforcement mechanism.

Meta describes both rate/data limits and behavioral detection in its
[anti-scraping explanation](https://about.fb.com/news/2021/04/how-we-combat-scraping/).
