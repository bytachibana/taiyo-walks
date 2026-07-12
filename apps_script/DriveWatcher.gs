/**
 * DriveWatcher.gs — Taiyo's Danville Walk Tracker
 * ================================================
 * Watches a Google Drive folder for new .gpx files and pushes each one to the
 * GitHub repo's walks/ directory via the GitHub REST API. Processed filenames
 * are recorded in a Drive file (processed_files.json) inside the same folder so
 * files are never pushed twice.
 *
 * Each file is (1) scrubbed of time-of-day (date kept) and (2) stored under a
 * date-based name: YYYY-MM-DD.gpx for a single walk, or YYYY-MM-DD-1.gpx,
 * -2.gpx ... when there are multiple walks on the same day. Neither the file
 * contents nor the filename reveals what time of day a walk happened.
 *
 * SETUP (Project Settings -> Script Properties):
 *   GH_PAT           Fine-grained PAT, Contents: read/write on the repo only
 *   REPO_OWNER       GitHub username
 *   REPO_NAME        taiyo-walks
 *   DRIVE_FOLDER_ID  ID of the Drive folder Runkeeper GPX files are saved to
 *
 * TRIGGER: time-driven, every 15 minutes -> watchDriveFolder()
 * Run watchDriveFolder() once manually first to authorize scopes and check logs.
 */

var PROCESSED_INDEX = 'processed_files.json';

function props_() {
  var p = PropertiesService.getScriptProperties();
  var cfg = {
    token:    p.getProperty('GH_PAT'),
    owner:    p.getProperty('REPO_OWNER'),
    repo:     p.getProperty('REPO_NAME'),
    folderId: p.getProperty('DRIVE_FOLDER_ID'),
  };
  var missing = Object.keys(cfg).filter(function (k) { return !cfg[k]; });
  if (missing.length) {
    throw new Error('Missing script properties: ' + missing.join(', '));
  }
  return cfg;
}

/** Entry point — set the 15-minute time trigger to call this. */
function watchDriveFolder() {
  var cfg = props_();
  var folder = DriveApp.getFolderById(cfg.folderId);

  var processed = loadProcessed_(folder);   // Set of filenames already pushed
  var pushed = 0;

  var files = folder.getFiles();
  while (files.hasNext()) {
    var file = files.next();
    var name = file.getName();

    if (!/\.gpx$/i.test(name)) continue;   // only .gpx
    if (processed[name]) continue;         // already handled

    try {
      pushToGitHub_(cfg, name, file.getBlob());
      processed[name] = true;
      pushed++;
      Logger.log('Pushed: ' + name);
    } catch (err) {
      // Log and continue — one bad file must not block the rest. It stays
      // unrecorded, so the next run retries it.
      Logger.log('FAILED ' + name + ': ' + err.message);
    }
  }

  if (pushed > 0) saveProcessed_(folder, processed);
  Logger.log('Done. Pushed ' + pushed + ' new file(s).');
}

/**
 * Remove time-of-day from GPX text but keep the date, so nothing published to
 * the public repo reveals when a walk happened:
 *   <time>2026-06-22T14:48:26Z</time>      -> <time>2026-06-22T00:00:00Z</time>
 *   <name><![CDATA[Walking 6/22/26 7:48 am]]> -> <name><![CDATA[Walking 6/22/26]]>
 */
function scrubGpx_(text) {
  return text
    .replace(/(<time>\d{4}-\d{2}-\d{2})T[^<]*(<\/time>)/g, '$1T00:00:00Z$2')
    .replace(/(<name>(?:<!\[CDATA\[)?[^<\]]*?)\s+\d{1,2}:\d{2}\s*[ap]m/gi, '$1');
}

// ── GitHub Contents API helpers ─────────────────────────────────────────────
function ghUrl_(cfg, path) {
  return 'https://api.github.com/repos/' + cfg.owner + '/' + cfg.repo +
         '/contents/' + path;
}
function ghHeaders_(cfg) {
  return { Authorization: 'token ' + cfg.token, Accept: 'application/vnd.github+json' };
}

/** List existing walks/ filenames of the form DATE.gpx or DATE-N.gpx. */
function listWalkNamesForDate_(cfg, date) {
  var resp = UrlFetchApp.fetch(ghUrl_(cfg, 'walks') + '?ref=main', {
    method: 'get', headers: ghHeaders_(cfg), muteHttpExceptions: true,
  });
  var code = resp.getResponseCode();
  if (code === 404) return [];   // walks/ doesn't exist yet
  if (code !== 200) throw new Error('list walks/ ' + code + ': ' + resp.getContentText());
  var re = new RegExp('^' + date + '(-\\d+)?\\.gpx$');
  return JSON.parse(resp.getContentText())
    .filter(function (e) { return e.type === 'file' && re.test(e.name); })
    .map(function (e) { return e.name; });
}

/**
 * Decide the target filename for a new walk on `date`:
 *   - no existing walk that day        -> DATE.gpx
 *   - one existing bare DATE.gpx        -> new is DATE-2.gpx, and the existing
 *                                          bare file is renamed to DATE-1.gpx
 *   - existing DATE-N.gpx files         -> new is DATE-(max+1).gpx
 * Returns { name: 'DATE[-N].gpx', migrate: {from, to} | null }.
 */
function resolveTarget_(cfg, date) {
  var names = listWalkNamesForDate_(cfg, date);
  var bare = date + '.gpx';
  var hasBare = names.indexOf(bare) !== -1;
  var suffixes = names
    .map(function (n) { var m = n.match(/-(\d+)\.gpx$/); return m ? parseInt(m[1], 10) : 0; })
    .filter(function (x) { return x > 0; });

  if (names.length === 0) return { name: bare, migrate: null };

  if (hasBare && suffixes.length === 0) {
    return { name: date + '-2.gpx', migrate: { from: bare, to: date + '-1.gpx' } };
  }

  var next = (suffixes.length ? Math.max.apply(null, suffixes) : 0) + 1;
  var target = { name: date + '-' + next + '.gpx', migrate: null };
  // Normalize a stray bare file next to suffixed ones, only if -1 is free.
  if (hasBare && suffixes.indexOf(1) === -1) {
    target.migrate = { from: bare, to: date + '-1.gpx' };
  }
  return target;
}

/** GET a file's { contentBase64, sha }. */
function ghGetFile_(cfg, name) {
  var resp = UrlFetchApp.fetch(ghUrl_(cfg, 'walks/' + name) + '?ref=main', {
    method: 'get', headers: ghHeaders_(cfg), muteHttpExceptions: true,
  });
  if (resp.getResponseCode() !== 200) {
    throw new Error('get ' + name + ' ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  var j = JSON.parse(resp.getContentText());
  return { contentBase64: (j.content || '').replace(/\n/g, ''), sha: j.sha };
}

/** PUT (create or update) walks/<name> with base64 content. */
function ghPutFile_(cfg, name, base64Content, message, sha) {
  var payload = { message: message, content: base64Content, branch: 'main' };
  if (sha) payload.sha = sha;
  var resp = UrlFetchApp.fetch(ghUrl_(cfg, 'walks/' + name), {
    method: 'put', headers: ghHeaders_(cfg), contentType: 'application/json',
    payload: JSON.stringify(payload), muteHttpExceptions: true,
  });
  var code = resp.getResponseCode();
  if (code !== 200 && code !== 201) {
    throw new Error('put ' + name + ' ' + code + ': ' + resp.getContentText());
  }
}

/** DELETE walks/<name>. */
function ghDeleteFile_(cfg, name, sha, message) {
  var resp = UrlFetchApp.fetch(ghUrl_(cfg, 'walks/' + name), {
    method: 'delete', headers: ghHeaders_(cfg), contentType: 'application/json',
    payload: JSON.stringify({ message: message, sha: sha, branch: 'main' }),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() !== 200) {
    throw new Error('delete ' + name + ' ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
}

/** Rename walks/from -> walks/to (copy content to new path, delete old). */
function ghRename_(cfg, from, to) {
  var f = ghGetFile_(cfg, from);
  ghPutFile_(cfg, to, f.contentBase64, 'Rename ' + from + ' -> ' + to, null);
  ghDeleteFile_(cfg, from, f.sha, 'Remove ' + from + ' (renamed to ' + to + ')');
}

/** Extract YYYY-MM-DD from the Drive filename, falling back to GPX <time>. */
function extractDate_(filename, content) {
  var m = filename.match(/(\d{4}-\d{2}-\d{2})/);
  if (m) return m[1];
  var c = content.match(/<time>(\d{4}-\d{2}-\d{2})/);
  return c ? c[1] : null;
}

/** Scrub time-of-day, choose a date-based name, and push the walk to GitHub. */
function pushToGitHub_(cfg, filename, blob) {
  var content = scrubGpx_(blob.getDataAsString());   // strip time-of-day first
  var date = extractDate_(filename, content);
  if (!date) throw new Error('no date found in ' + filename);

  var target = resolveTarget_(cfg, date);
  if (target.migrate) ghRename_(cfg, target.migrate.from, target.migrate.to);

  var base64 = Utilities.base64Encode(content, Utilities.Charset.UTF_8);
  ghPutFile_(cfg, target.name, base64, 'Add walk: ' + target.name, null);
  Logger.log('  stored as ' + target.name +
             (target.migrate ? ' (renamed ' + target.migrate.from + ' -> ' + target.migrate.to + ')' : ''));
}

/** Load processed_files.json from the folder; returns an object used as a set. */
function loadProcessed_(folder) {
  var it = folder.getFilesByName(PROCESSED_INDEX);
  if (!it.hasNext()) return {};
  try {
    var data = JSON.parse(it.next().getBlob().getDataAsString());
    var set = {};
    (data.processed || []).forEach(function (n) { set[n] = true; });
    return set;
  } catch (err) {
    Logger.log('Could not parse ' + PROCESSED_INDEX + ', starting fresh: ' + err.message);
    return {};
  }
}

/** Write processed_files.json back to the folder (create or overwrite). */
function saveProcessed_(folder, processed) {
  var json = JSON.stringify({ processed: Object.keys(processed).sort() }, null, 2);
  var it = folder.getFilesByName(PROCESSED_INDEX);
  if (it.hasNext()) {
    it.next().setContent(json);
  } else {
    folder.createFile(PROCESSED_INDEX, json, MimeType.PLAIN_TEXT);
  }
}
