# Instagram account incident and operating rules

## Current state — September 27, 2026

**The user reports normal account access returned after opening Instagram on their phone and tapping Dismiss on an automated-behavior warning. Automated Instagram extraction remains paused.** Accounts Center had previously displayed Locked. The latest report supersedes that earlier current-state description, but does not erase the restriction or justify restarting collection.

This page takes precedence over older cookie refresh, browser-playback and resume instructions. Read it before any Instagram collection. Private screenshots, logs, queue IDs and detailed evidence belong in the gitignored incident directory referenced by `AGENTS.local.md`; they are not published here.

## Observed sequence

| Stage | Evidence and limits |
|---|---|
| Earlier incidents | An August batch and same-run retries preceded a login checkpoint. Later spaced requests also failed. Download success did not establish safety. |
| September 25 bulk run | Logs record 71 fresh successful authenticated download jobs in about 85 minutes. The private runner enforced only a 45-second gap between jobs. One job can issue multiple HTTP requests; the exact HTTP total is unknown. |
| September 27 troubleshooting | An invalid-cookie/empty-media failure prompted one attended export. A different item then failed HTTP 400. A relevant yt-dlp update was installed, but a further unattempted item still failed HTTP 400. Collection stopped. Those errors alone did not identify their cause. |
| Email checkpoint | The user reported a warning that the recovery email might be insecure and a request to supply a different address; the user reported changing it. This did not prove mailbox compromise or that changing email was the necessary remedy. |
| Account restriction | A user-supplied Accounts Center screenshot explicitly marked the personal Instagram profile Locked. It did not specify duration or establish permanent disablement. |
| Phone notice | A second screenshot explicitly stated **“We suspect automated behavior on your account”** and warned of possible temporary restriction or permanent disablement. It displayed a Dismiss button. |
| User recovery | The user reports tapping Dismiss on the phone and no longer being locked. The agent did not independently test account access. This was an observed recovery in this incident, not a guaranteed fix for future locks. |

The explicit phone warning confirms Instagram suspected automation. Our authenticated bulk activity likely contributed; Instagram did not disclose the exact detection signals or establish that every earlier HTTP 400 shared that cause. Do not claim certainty about the trigger, an email compromise, a permanent ban, or a safe waiting period.

## What went wrong

The collection design tied automated requests to a personal account through exported login cookies. The bulk runner's volume was too aggressive given earlier warnings. Cookie repair, downloader upgrades, changing browsers and slowing jobs retained that underlying account exposure. Successful downloads and restored browser playback were treated as sufficient grounds to continue when they were not. The agent should have stopped account-facing collection and reviewed the capture design after the earlier warning.

The user bore the disruption, including a recovery-email change and a Locked state. Finishing the queue does not take priority over account access. Do not present account-security changes as routine extractor maintenance.

## Required behavior for future agents

1. **Keep the persistent hold.** Inspect `igx ig-safety status` before any account-facing work. Record new warning evidence with `igx ig-safety hold --reason '<brief incident summary>'`; preserve existing request history. If inspecting/updating state needs filesystem permission, obtain it or read the state without modifying it and remain stopped. Never use a new state path or delete history to make a job run.
2. **Separate recovery from resumption.** Record “user reports unlocked” when appropriate. Do not interpret Dismiss, working reels, a changed email, elapsed time, unused daily slots, or “finish the queue” as approval to restart after this incident. Existing broad extraction permission does not resolve a newly discovered account risk. A future change needs explicit user direction addressing the incident and the proposed capture approach.
3. **Do not bypass the hold.** No direct yt-dlp probe, cookie export, signed-in browser/CDN fallback, alternate profile/account, proxy rotation, custom download loop or unattended retry. Keep delegated agents within the same boundary. User recovery activity is separate from agent collection.
4. **Let the user handle security recovery.** Do not submit login forms or change email/password/security settings merely to repair extraction. Do not request verification codes or passwords in chat. Record only necessary facts and avoid retaining challenge URL tokens.
5. **Continue useful local work.** Analyze already downloaded or user-supplied media, preserve source URLs and saved prompts, file actual findings, and retain requested audio. Local analysis may run concurrently. Leave sources needing unavailable media incomplete; failed is not completed, and unattempted rows must not be bulk marked failed.
6. **Make handoffs explicit.** Write the latest account status, evidence basis, remaining queue counts, hold status, and actions not authorized into the local handoff. A cleared warning must not remove the incident from that record. No automatic wakeup or scheduled retry should be created just to try again later.

## Software controls and their limits

The current wrapper serializes Instagram jobs across processes, enforces a 180-second minimum gap and an eight-job rolling-24-hour cap, and holds after nonzero download exits. These are volume brakes, **not verified safe allowances**. They count downloader invocations, not every underlying HTTP request. A detected warning is a stop even if downloads succeed.

`igx ig-safety resume --confirmed-clear` is a technical state change, not a safety determination or user approval. The flag cannot validate the incident review required above. Do not call it just because the user reports recovery. The local hold survives shells and checkouts on the same machine, but does not automatically transfer to another host and cannot intercept direct external tools. A new clone/host therefore must honor this document and the private handoff before account access, even if it has no hold file yet.

No downloader code or limits were changed by this documentation update. The incident hold remains active. Never test these instructions by contacting Instagram.

## Preferred replacement to evaluate

Keep URL, saved time and user intent in the queue. Remove personal Instagram credentials from automated retrieval. Reuse existing files or available source material; provide intake for a user-supplied video, recording, screenshots or creator file when the source cannot otherwise be obtained. Transcription, visual analysis, audio retention and knowledge filing can then operate locally.

A third-party saving app is not evidence of guaranteed full-media retrieval or safe use of a personal account. Verify the actual export contains the requested video/audio and source reference; a preview or bookmarked URL is insufficient for transcription or music capture. This is a proposed redesign, not an implemented fallback or authorization to send private material to a service.

## Related guidance

- [Agent entry point](../AGENTS.md)
- [Authentication reference](auth.md)
- [Extraction workflow](agent-workflow.md)
- [Meta's explanation of rate limits and behavioral detection](https://about.fb.com/news/2021/04/how-we-combat-scraping/) — general mechanism, not a diagnosis of this account.
