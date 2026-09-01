/**
 * sheet-trigger.gs — make the website actually live.
 *
 * THE PROBLEM THIS SOLVES
 * The site rebuilds on a GitHub Actions cron that claims to run every 30
 * minutes. It does not. GitHub throttles scheduled workflows on the free tier
 * and DROPS runs rather than queueing them. Measured over 100 consecutive runs
 * (2026-08-24 to 09-01): median gap 53 minutes, 90th percentile 5.6 hours,
 * worst 11.8 hours. Two gaps in five were over an hour.
 *
 * So "I updated the sheet, why isn't it on the site" had a real answer, and it
 * was not one anybody could predict from the cron line.
 *
 * THE FIX: stop polling, start pushing. This script lives inside the workbook.
 * When Grant edits a grade it tells GitHub to rebuild immediately, via
 * `repository_dispatch` -- which, unlike `schedule`, GitHub does not throttle.
 * Edit to live site: about a minute.
 *
 * DEBOUNCED, DELIBERATELY. Typing a row of grades fires onEdit for every cell.
 * Dispatching each one would queue a hundred builds that each deploy over the
 * last. Instead an edit sets a flag and arms a single one-shot timer; every
 * further edit inside that window rides the same timer. One build per burst,
 * and no edit is ever dropped -- an edit arriving during a build arms the next
 * timer rather than being swallowed.
 *
 * INSTALLATION: see tools/SHEET-TRIGGER-SETUP.md. Roughly, paste this into
 * Extensions > Apps Script, add a GITHUB_TOKEN script property, and add an
 * INSTALLABLE on-edit trigger (the simple onEdit cannot make network calls).
 */

var REPO      = 'GrantSanders05/the-model-record';
var EVENT     = 'sheet-edited';
var QUIET_SEC = 60;     // wait this long after an edit before building

/** Installable on-edit trigger points here. Cheap by design: it only arms. */
function onSheetEdit(e) {
  var props = PropertiesService.getScriptProperties();
  props.setProperty('dirty', '1');

  // A timer is already counting down; this edit rides it.
  if (props.getProperty('armed') === '1') return;

  props.setProperty('armed', '1');
  ScriptApp.newTrigger('fireBuild')
    .timeBased()
    .after(QUIET_SEC * 1000)
    .create();
}

/** The one-shot timer lands here. Dispatches, then removes itself. */
function fireBuild() {
  var props = PropertiesService.getScriptProperties();
  try {
    if (props.getProperty('dirty') === '1') {
      props.deleteProperty('dirty');
      dispatch();
    }
  } finally {
    // Always disarm and always clean up, even if the dispatch threw. A trigger
    // that leaks stays in the project forever and Apps Script caps them at 20;
    // a stuck `armed` flag would mean no edit ever builds again. Both failures
    // are silent, which is why this is in a finally.
    props.deleteProperty('armed');
    var here = ScriptApp.getProjectTriggers();
    for (var i = 0; i < here.length; i++) {
      if (here[i].getHandlerFunction() === 'fireBuild') {
        ScriptApp.deleteTrigger(here[i]);
      }
    }
  }
}

function dispatch() {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    // Loud, because the alternative is a script that appears installed and
    // silently does nothing -- which is the exact failure it exists to fix.
    throw new Error('GITHUB_TOKEN script property is not set. ' +
                    'Project Settings > Script properties.');
  }
  var res = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + REPO + '/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/vnd.github+json'
      },
      payload: JSON.stringify({
        event_type: EVENT,
        client_payload: { at: new Date().toISOString() }
      }),
      muteHttpExceptions: true
    });
  var code = res.getResponseCode();
  if (code !== 204) {
    throw new Error('GitHub returned ' + code + ': ' + res.getContentText());
  }
  console.log('build dispatched');
}

/** Run this once by hand to prove the token works before trusting the trigger. */
function testDispatch() {
  dispatch();
  console.log('If the Actions tab shows a run starting, setup is complete.');
}
