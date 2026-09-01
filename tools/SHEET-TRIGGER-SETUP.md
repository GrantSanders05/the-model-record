# Make the site update the moment you edit the sheet

**Five minutes, once. Free.**

## Why this exists

The site rebuilds on a GitHub Actions cron set to every 30 minutes. It does not
run every 30 minutes. GitHub throttles scheduled workflows on free accounts and
*drops* runs rather than queueing them. Over 100 consecutive runs
(24 Aug – 1 Sep 2026):

| | gap between rebuilds |
|---|---|
| median | **53 minutes** |
| 90th percentile | **5.6 hours** |
| worst | **11.8 hours** |
| over an hour | **40% of the time** |

So a grade edited on Saturday morning could genuinely not appear until Saturday
evening. Nothing was broken — the schedule just isn't a promise GitHub keeps.

The fix inverts it. Instead of GitHub checking the sheet every so often, **the
sheet tells GitHub the moment you change something.** That path
(`repository_dispatch`) is not throttled. Edit → live in about a minute.

The 30-minute cron stays as a backstop, so if this ever breaks the site still
refreshes on its own.

---

## Step 1 — Make a token (2 min)

1. Go to **github.com → your avatar → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token**
2. Name it `sheet-trigger`
3. **Expiration:** 1 year (put a reminder in your calendar — an expired token
   fails silently apart from an emailed error from Google)
4. **Repository access:** Only select repositories → `the-model-record`
5. **Permissions → Repository permissions → Contents: Read and write**
   *(this is what `repository_dispatch` requires — nothing else needs enabling)*
6. Generate, and copy the token. You cannot see it again.

> This token can write to that one repo, nothing else. It never touches the
> grades — it only says "rebuild now."

## Step 2 — Put the script in the sheet (2 min)

1. Open the grades workbook → **Extensions → Apps Script**
2. Delete whatever is in `Code.gs`, paste in all of
   [`tools/sheet-trigger.gs`](sheet-trigger.gs)
3. **Project Settings** (gear, left side) → **Script properties** →
   **Add script property**
   - Property: `GITHUB_TOKEN`
   - Value: the token from step 1
   - Save

## Step 3 — Arm it (1 min)

1. Still in Apps Script: **Triggers** (the clock icon, left side) →
   **Add Trigger**
2. Set it to:
   - Function: **`onSheetEdit`**
   - Event source: **From spreadsheet**
   - Event type: **On edit**
3. Save. Google will ask you to authorize — approve it.

> **This must be added as a trigger here, not left as a plain `onEdit`
> function.** Google's automatic simple triggers are forbidden from making
> network calls, so a script that looks correct would quietly never fire.

## Step 4 — Prove it works

In the Apps Script editor, pick **`testDispatch`** from the function dropdown
and press **Run**. Then open the repo's **Actions** tab — a
*"refresh grades & republish"* run should appear within a few seconds.

If it does, edit any grade cell in the sheet and watch a second run start about
a minute later. That's it.

---

## How you'll know it's working from then on

The site's timestamp now reads **"Generated automatically · 4 minutes ago"** in
your own timezone, and turns amber with a warning once the page is more than
three hours old. You never have to guess whether what you're looking at
includes this morning's edits — the page says.

## If it stops

- **Google emails you** when an Apps Script trigger throws, so a dead token or
  a revoked permission surfaces rather than going quiet.
- The 30-minute cron is still running underneath, so worst case you're back to
  the old behaviour, not to a frozen site.
- Check **Apps Script → Executions** for the error.
