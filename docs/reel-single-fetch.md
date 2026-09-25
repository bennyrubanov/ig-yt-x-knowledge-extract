# One yt-dlp invocation per reel

The reel command previously launched yt-dlp three times: resolve the ID, fetch the
caption, then download. When Instagram returned empty media, the first two failures
were ignored and the final download hit the same source again. Batch spacing did not
separate these internal requests.

The command now parses direct `/reel/`, `/reels/`, `/p/` and `/tv/` IDs locally and
asks one yt-dlp invocation to save video, description, info JSON and thumbnail.
Share/redirect URLs use yt-dlp's ID in the output template during that same download.
Descriptions retain the existing `{id}.description.txt` path, and audio, frames,
OCR, Whisper and wrapper output keep their existing names.

A nonzero downloader exit, absent video or zero-byte video stops local processing;
an old video cannot turn a failed network attempt into success. A failed attempt
does not create an empty `.description.txt` that the queue could mistake for usable
content. Metadata is read from downloaded sidecars with no network fallback. The
shared yt-dlp helper still uses a throwaway cookie copy and deletes it after use.

This reduces repeat extraction invocations; it does **not** mean there is only one
HTTP request. yt-dlp can make its own metadata, thumbnail and media requests. Keep
one Instagram worker, the existing gap/backoff, and the stop-on-empty-media rule.

Validation uses mocked subprocesses, including cookie write-back, failed downloads
with stale files, the four direct URL forms, redirect IDs and the downstream local
pipeline. No live Instagram probes are needed to run the tests:

```bash
python3 -m unittest discover -s lib -p 'test_*.py'
```
