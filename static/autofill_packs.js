/* ==========================================================================
   CairoAI — Tender Autofill Packs
   --------------------------------------------------------------------------
   The manual-upload path into Agent Autofill. A user groups the documents for
   one tender into a "pack", submits it, and the agent pre-fills what it can.

   THE ONE RULE THIS FILE ENFORCES: drafts only.

   Nothing here ever calls a pack complete, ready, final or submission-ready,
   because none of those are true of anything this system produces — a
   signature, a price and a declaration are never ours to write. The status
   vocabulary below is deliberately narrow for that reason, and every state
   that could be mistaken for "done" carries the word DRAFT.

   A half-reviewed pack is distinct from a fully-reviewed one in BOTH
   directions:
     visually    — a gold IN REVIEW chip with an "n of m" count and a partially
                   filled meter, against the grey DRAFT REVIEWED chip;
     functionally — export is refused, and the refusal names the documents and
                   the count that are holding it up rather than greying a
                   button out silently.

   RENDERING
   DOM nodes and textContent, never innerHTML. Filenames, pack names, field
   labels and the agent's own reason strings are user- or third-party-supplied
   and are not parsed as markup anywhere in this app (CLAUDE.md).

   IDENTITY
   The session cookie carries it. No X-Company-ID header is sent — the client
   choosing its own tenant was the hole that authentication closed.
   ========================================================================== */
(function (global) {
    'use strict';

    var API = '/api/autofill-packs';

    // Tight enough that a multi-document pack visibly advances rather than
    // arriving as one block at the end.
    var POLL_MS = 700;           // status cadence while processing
    var POLL_MAX_FAILS = 5;      // consecutive network failures before we stop
    var SLOW_AFTER_MS = 180000;  // 3 min — past here, say so rather than spin

    /* Words a person reaches for to wave a whole review through. The server
       rejects these by name (agent_autofill/integration/review_gate.py); the
       client rejects them too so the refusal arrives before the round trip. */
    var BLANKET_NOTES = [
        '*', 'all', 'any', 'everything', 'every', 'each', '-', 'none',
        'yes', 'ok', 'okay', 'confirm', 'confirmed', 'agree', 'i agree',
        'all fields'
    ];

    /* Every state a pack can be in, and what we are willing to call it.
       `reviewed` is the one worth reading twice: the flags have all been
       acknowledged, and the pack is still a draft. */
    var STATUS = {
        processing:   { label: 'PROCESSING',     cls: 'is-processing' },
        needs_review: { label: 'NEEDS REVIEW',   cls: 'is-needs-review' },
        reviewed:     { label: 'DRAFT REVIEWED', cls: 'is-reviewed' },
        error:        { label: 'ERROR',          cls: 'is-error' }
    };

    var state = {
        packs: [],
        draft: null,      // { pack_id, pack_name, files: [] } — not yet submitted
        detail: null,     // the pack open in the review drawer
        poll: null,
        loaded: false
    };

    /* ------------------------------------------------------------- dom utils */

    function $(id) { return document.getElementById(id); }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null) { n.textContent = String(text); }
        return n;
    }

    function clear(node) {
        while (node && node.firstChild) { node.removeChild(node.firstChild); }
    }

    function show(node, visible) {
        if (!node) { return; }
        if (visible) { node.removeAttribute('hidden'); }
        else { node.setAttribute('hidden', 'hidden'); }
    }

    function basename(path) {
        var s = String(path || '');
        var cut = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
        return cut >= 0 ? s.slice(cut + 1) : s;
    }

    function formatBytes(n) {
        var b = Number(n);
        if (!isFinite(b) || b <= 0) { return ''; }
        if (b < 1024) { return b + ' B'; }
        if (b < 1048576) { return (b / 1024).toFixed(0) + ' KB'; }
        return (b / 1048576).toFixed(1) + ' MB';
    }

    function formatDate(value) {
        if (!value) { return '—'; }
        var d = new Date(value);
        if (isNaN(d.getTime())) { return String(value); }   // show what we got
        return d.toLocaleDateString(undefined, {
            day: '2-digit', month: 'short', year: 'numeric'
        }) + ' ' + d.toLocaleTimeString(undefined, {
            hour: '2-digit', minute: '2-digit'
        });
    }

    function plural(n, one, many) { return n === 1 ? one : many; }

    /* ------------------------------------------------------------------- api */

    function ApiError(message, status) {
        this.name = 'ApiError';
        this.message = message;
        this.status = status;
    }
    ApiError.prototype = Object.create(Error.prototype);

    async function api(path, opts) {
        var init = opts || {};
        // Stated rather than relied on: a future move to another origin should
        // fail loudly instead of quietly dropping the session cookie.
        init.credentials = 'same-origin';
        var res;
        try {
            res = await fetch(path, init);
        } catch (e) {
            throw new ApiError('Could not reach the server.', 0);
        }
        if (res.status === 401) {
            // The session went away mid-visit. Put the gate back rather than
            // reporting this as a feature failure.
            if (global.CairoAuth) { global.CairoAuth.handleUnauthorized(); }
            throw new ApiError('Your session ended. Sign in again.', 401);
        }
        if (!res.ok) {
            var detail = '';
            try {
                var body = await res.json();
                detail = body.detail || body.error || body.message || '';
            } catch (e2) { /* no JSON body */ }
            throw new ApiError(detail || ('The server refused that (HTTP ' + res.status + ').'),
                               res.status);
        }
        if (res.status === 204) { return null; }
        try { return await res.json(); } catch (e3) { return null; }
    }

    function sendJson(path, method, body) {
        return api(path, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
    }

    /* --------------------------------------------------------- normalisation
       The contract fixes the routes and the request bodies. It does not fix
       every response field name, and the backend is being built in parallel,
       so reads accept the plausible spellings rather than breaking on one.
       Writes send exactly what the contract specifies and nothing else. */

    function pick(obj, keys, dflt) {
        for (var i = 0; i < keys.length; i++) {
            var v = obj ? obj[keys[i]] : undefined;
            if (v !== undefined && v !== null) { return v; }
        }
        return dflt;
    }

    function asList(value) {
        if (Array.isArray(value)) { return value; }
        if (value && Array.isArray(value.items)) { return value.items; }
        return [];
    }

    function normalisePack(raw) {
        raw = raw || {};
        var files = pick(raw, ['files', 'documents'], null);
        return {
            pack_id: String(pick(raw, ['pack_id', 'id'], '')),
            pack_name: String(pick(raw, ['pack_name', 'name'], '') || 'Untitled pack'),
            status: String(pick(raw, ['status'], 'processing')),
            file_count: Number(pick(raw, ['file_count', 'files_total', 'files_count'],
                                    Array.isArray(files) ? files.length : 0)) || 0,
            submitted_at: pick(raw, ['submitted_at', 'submitted', 'created_at'], null),
            files_done: Number(pick(raw, ['files_done'], 0)) || 0,
            files_total: Number(pick(raw, ['files_total', 'file_count'], 0)) || 0,
            error_reason: pick(raw, ['error_reason'], '') || '',
            flags_total: Number(pick(raw, ['flags_total', 'flagged_count'], 0)) || 0,
            flags_open: Number(pick(raw, ['flags_open', 'outstanding_count'], 0)) || 0
        };
    }

    function normaliseFile(raw) {
        raw = raw || {};
        return {
            file_id: String(pick(raw, ['file_id', 'id'], '')),
            // 'original_filename' is what the pack API actually returns. Without it
            // first in this list every file in a pack rendered as the literal
            // word "document" — the fallback — so a user could not tell which
            // file they were about to remove.
            filename: basename(pick(raw, ['original_filename', 'filename', 'file_name',
                                          'name', 'path'], 'document')),
            size: Number(pick(raw, ['size', 'bytes', 'size_bytes'], 0)) || 0
        };
    }

    function normaliseItem(raw) {
        raw = raw || {};
        var ackAt = pick(raw, ['acknowledged_at', 'acknowledgedAt'], null);
        var key = String(pick(raw, ['item_key', 'key', 'field_key'], ''));
        return {
            item_key: key,
            label: String(pick(raw, ['label', 'field_label', 'name'], '') || key),
            location: String(pick(raw, ['location', 'where', 'page'], '') || ''),
            category: String(pick(raw, ['category', 'kind', 'type'], '') || ''),
            reason: String(pick(raw, ['reason', 'why', 'detail'], '') || ''),
            acknowledged: ackAt ? true : Boolean(pick(raw, ['acknowledged'], false)),
            note: String(pick(raw, ['acknowledged_note', 'note'], '') || '')
        };
    }

    function normaliseFill(raw) {
        raw = raw || {};
        var key = String(pick(raw, ['item_key', 'key'], ''));
        return {
            item_key: key,
            label: String(pick(raw, ['label', 'field_label'], '') || key),
            value: String(pick(raw, ['value'], '') || ''),
            location: String(pick(raw, ['location', 'where'], '') || '')
        };
    }

    function normaliseDocument(raw) {
        raw = raw || {};
        var name = pick(raw, ['document', 'document_name', 'source_document',
                              'filename', 'file_name', 'source_path'], 'Document');
        return {
            review_id: String(pick(raw, ['review_id', 'reviewId', 'id'], '')),
            document: basename(name) || 'Document',
            items: asList(pick(raw, ['items', 'flags', 'flagged_fields', 'fields',
                                     'outstanding'], [])).map(normaliseItem),
            fills: asList(pick(raw, ['fills', 'filled_values', 'filled', 'values'], []))
                       .map(normaliseFill),
            values_confirmed: Boolean(pick(raw, ['values_confirmed', 'values_confirmed_at'],
                                           false))
        };
    }

    /* A flat list of flags, each naming its own document, is grouped back into
       documents — the review screen is organised by the document a field came
       from, so that grouping has to exist whichever shape arrives. */
    function groupFlatFlags(flags) {
        var order = [];
        var byKey = {};
        flags.forEach(function (raw) {
            var reviewId = String(pick(raw, ['review_id', 'reviewId'], ''));
            var doc = basename(pick(raw, ['document', 'document_name', 'source_document',
                                          'filename', 'source_path'], 'Document'));
            var key = reviewId || doc;
            if (!byKey[key]) {
                byKey[key] = { review_id: reviewId, document: doc || 'Document',
                               items: [], fills: [], values_confirmed: true };
                order.push(key);
            }
            byKey[key].items.push(normaliseItem(raw));
        });
        return order.map(function (k) { return byKey[k]; });
    }

    function normaliseDetail(raw) {
        raw = raw || {};
        var pack = normalisePack(raw);
        pack.files = asList(pick(raw, ['files'], [])).map(normaliseFile);

        var docs = pick(raw, ['documents', 'reviews', 'flagged_documents'], null);
        if (Array.isArray(docs)) {
            pack.documents = docs.map(normaliseDocument);
        } else {
            var flat = asList(pick(raw, ['flags', 'flagged_fields', 'items'], []));
            pack.documents = flat.length ? groupFlatFlags(flat) : [];
        }
        pack.download_url = pick(raw, ['download_url', 'export_url'], '') || '';
        return pack;
    }

    /* The whole-pack verdict. Export needs every flagged field acknowledged
       AND, for any document that reports auto-filled values, those values
       confirmed — acknowledging only the fields the agent could not fill would
       leave the ones it DID fill unread, which is the gap confirm-values
       exists to close. Documents that report no values are not held to it. */
    function reviewState(detail) {
        var totals = { flags: 0, acked: 0, open: 0, valuesPending: 0, byDoc: [] };
        (detail.documents || []).forEach(function (doc) {
            var open = 0;
            doc.items.forEach(function (item) {
                totals.flags += 1;
                if (item.acknowledged) { totals.acked += 1; } else { open += 1; }
            });
            var valuesPending = doc.fills.length > 0 && !doc.values_confirmed;
            if (valuesPending) { totals.valuesPending += 1; }
            if (open || valuesPending) {
                totals.byDoc.push({ document: doc.document, open: open,
                                    valuesPending: valuesPending });
            }
        });
        totals.open = totals.flags - totals.acked;
        totals.exportable = (detail.documents || []).length > 0 &&
                            totals.open === 0 && totals.valuesPending === 0;
        return totals;
    }

    function noteProblem(note) {
        var trimmed = String(note || '').trim();
        if (trimmed.length < 3) {
            return 'Write a short note saying what you checked on this field.';
        }
        if (BLANKET_NOTES.indexOf(trimmed.toLowerCase()) !== -1) {
            return 'That acknowledges everything at once. Say what you checked '
                 + 'about this field in particular.';
        }
        return null;
    }

    /* Server-supplied. Anything that is not a same-origin path or an http(s)
       URL is refused rather than put in an href — javascript: in a download
       link is a script this page would run on click. */
    function safeUrl(url) {
        var s = String(url || '').trim();
        if (!s) { return null; }
        if (s.charAt(0) === '/' && s.charAt(1) !== '/') { return s; }
        if (/^https?:\/\//i.test(s)) { return s; }
        return null;
    }

    /* ------------------------------------------------------------ status pill */

    function statusMeta(status) {
        return STATUS[status] ||
               { label: String(status || 'unknown').toUpperCase().replace(/_/g, ' '),
                 cls: 'is-unknown' };
    }

    /* One pill for the whole app. A pack part-way through its review reads
       "IN REVIEW · 4/11" and keeps the gold treatment; only a pack with every
       flag acknowledged gets the settled grey one. */
    function statusPill(pack) {
        var meta = statusMeta(pack.status);
        var label = meta.label;
        if (pack.status === 'needs_review' && pack.flags_total > 0 &&
            pack.flags_open < pack.flags_total) {
            label = 'IN REVIEW · ' +
                    (pack.flags_total - pack.flags_open) + '/' + pack.flags_total;
        }
        var pill = el('span', 'ap-pill ' + meta.cls, label);
        return pill;
    }

    /* ------------------------------------------------------------- the drafts */

    function defaultPackName() {
        var now = new Date();
        return 'Tender pack — ' + now.toLocaleDateString(undefined, {
            day: '2-digit', month: 'short', year: 'numeric'
        });
    }

    function composerMessage(text, kind) {
        var node = $('apComposerMsg');
        if (!node) { return; }
        node.textContent = text || '';
        node.className = 'ap-msg' + (kind ? ' ' + kind : '');
        show(node, Boolean(text));
    }

    async function newPack() {
        var btn = $('apNewPackBtn');
        if (btn) { btn.disabled = true; }
        composerMessage('', '');
        try {
            var created = await api(API, { method: 'POST' });
            var packId = String(pick(created || {}, ['pack_id', 'id'], ''));
            if (!packId) { throw new ApiError('The server did not return a pack id.', 0); }
            state.draft = { pack_id: packId, pack_name: defaultPackName(), files: [] };
            var nameInput = $('apPackName');
            if (nameInput) { nameInput.value = state.draft.pack_name; }
            show($('apComposer'), true);
            renderComposer();
            // Name it straight away so the pack is never nameless server-side.
            saveName(state.draft.pack_name);
            if (nameInput) { nameInput.focus(); nameInput.select(); }
        } catch (e) {
            composerMessage('Could not start a pack: ' + e.message, 'is-bad');
            show($('apComposer'), true);
        } finally {
            if (btn) { btn.disabled = false; }
        }
    }

    var nameTimer = null;
    function scheduleSaveName() {
        var input = $('apPackName');
        if (!input || !state.draft) { return; }
        state.draft.pack_name = input.value;
        if (nameTimer) { clearTimeout(nameTimer); }
        nameTimer = setTimeout(function () { saveName(input.value); }, 600);
    }

    async function saveName(name) {
        if (!state.draft) { return; }
        var trimmed = String(name || '').trim() || defaultPackName();
        try {
            await sendJson(API + '/' + encodeURIComponent(state.draft.pack_id),
                           'PATCH', { pack_name: trimmed });
        } catch (e) {
            composerMessage('The name is not saved yet: ' + e.message, 'is-warn');
        }
    }

    /* One request carrying every file, per the contract's "MULTIPLE per
       request". The drop zone and ADD FILE both land here. */
    async function addFiles(fileList) {
        var files = Array.prototype.slice.call(fileList || []);
        if (!files.length || !state.draft) { return; }

        var zone = $('apDropZone');
        if (zone) { zone.classList.add('is-busy'); }
        composerMessage('Adding ' + files.length + ' ' +
                        plural(files.length, 'file', 'files') + '…', '');

        var form = new FormData();
        files.forEach(function (f) { form.append('files', f); });

        try {
            await api(API + '/' + encodeURIComponent(state.draft.pack_id) + '/files',
                      { method: 'POST', body: form });
            await refreshDraftFiles();
            composerMessage(files.length + ' ' + plural(files.length, 'file', 'files') +
                            ' added.', 'is-ok');
        } catch (e) {
            composerMessage('Nothing was added: ' + e.message, 'is-bad');
        } finally {
            if (zone) { zone.classList.remove('is-busy'); }
            var input = $('apFileInput');
            if (input) { input.value = ''; }   // let the same file be picked again
            renderComposer();
        }
    }

    /* The server's list is the one shown. An optimistic local list would drift
       from the file_ids the remove control has to send. */
    async function refreshDraftFiles() {
        if (!state.draft) { return; }
        try {
            var detail = await api(API + '/' + encodeURIComponent(state.draft.pack_id));
            var norm = normaliseDetail(detail);
            if (norm.files.length || !state.draft.files.length) {
                state.draft.files = norm.files;
            }
        } catch (e) { /* keep what we have; the message above already reported */ }
    }

    async function removeFile(file) {
        if (!state.draft) { return; }
        composerMessage('', '');
        try {
            await api(API + '/' + encodeURIComponent(state.draft.pack_id) +
                      '/files/' + encodeURIComponent(file.file_id),
                      { method: 'DELETE' });
            state.draft.files = state.draft.files.filter(function (f) {
                return f.file_id !== file.file_id;
            });
        } catch (e) {
            composerMessage('Could not remove that file: ' + e.message, 'is-bad');
        }
        renderComposer();
    }

    async function discardDraft() {
        if (!state.draft) { return; }
        var files = state.draft.files.slice();
        composerMessage('Discarding…', '');
        for (var i = 0; i < files.length; i++) {
            try {
                await api(API + '/' + encodeURIComponent(state.draft.pack_id) +
                          '/files/' + encodeURIComponent(files[i].file_id),
                          { method: 'DELETE' });
            } catch (e) { /* best effort — the pack is left empty either way */ }
        }
        closeComposer();
    }

    function closeComposer() {
        state.draft = null;
        show($('apComposer'), false);
        composerMessage('', '');
        clear($('apFileList'));
    }

    function renderComposer() {
        var list = $('apFileList');
        var submit = $('apSubmitBtn');
        var blocked = $('apSubmitBlocked');
        if (!list) { return; }
        clear(list);

        var files = state.draft ? state.draft.files : [];

        files.forEach(function (file) {
            var row = el('div', 'ap-file');

            var name = el('span', 'ap-file-name', file.filename);
            name.title = file.filename;
            row.appendChild(name);

            var size = formatBytes(file.size);
            if (size) { row.appendChild(el('span', 'ap-file-size', size)); }

            var remove = el('button', 'ap-file-remove', '×');
            remove.type = 'button';
            remove.setAttribute('aria-label', 'Remove ' + file.filename + ' from this pack');
            remove.addEventListener('click', function () { removeFile(file); });
            row.appendChild(remove);

            list.appendChild(row);
        });

        if (!files.length) {
            list.appendChild(el('p', 'ap-file-none', 'No documents in this pack yet.'));
        }

        // Submit is disabled until at least one file is in the pack, and the
        // reason is written out rather than left to a greyed-out button.
        var ready = files.length > 0;
        if (submit) { submit.disabled = !ready; }
        if (blocked) {
            blocked.textContent = ready
                ? ''
                : 'Add at least one document before submitting.';
            show(blocked, !ready);
        }
    }

    async function submitPack() {
        if (!state.draft || !state.draft.files.length) { return; }
        var packId = state.draft.pack_id;
        var submit = $('apSubmitBtn');
        if (submit) { submit.disabled = true; }
        composerMessage('Submitting…', '');
        try {
            if (nameTimer) { clearTimeout(nameTimer); nameTimer = null; }
            await saveName(state.draft.pack_name);
            await api(API + '/' + encodeURIComponent(packId) + '/submit',
                      { method: 'POST' });
            // Open the narration before polling, so the first line is on
            // screen while the server is still opening the first document.
            if (window.AgentNarration) {
                window.AgentNarration.start(state.draft && state.draft.pack_name);
            }
            closeComposer();
            await loadPacks();
            startPolling(packId);
        } catch (e) {
            composerMessage('Submit failed: ' + e.message, 'is-bad');
            if (submit) { submit.disabled = false; }
        }
    }

    /* --------------------------------------------------------------- polling */

    function stopPolling() {
        if (state.poll && state.poll.timer) { clearTimeout(state.poll.timer); }
        state.poll = null;
    }

    function startPolling(packId) {
        if (!packId) { return; }
        stopPolling();
        // lastSeq drives the ?since= cursor; usageShown stops the cost line
        // being repeated on every poll after the work finishes.
        state.poll = { packId: packId, started: Date.now(), fails: 0, timer: null,
                       lastSeq: 0, usageShown: false,
                       stopped: false };
        renderStatus({ status: 'processing', files_done: 0, files_total: 0 }, null);
        pollOnce();
    }

    /* Read the server's own record of what happened into the agent chat.

       These are not timed guesses. Each line was written by the code that did
       the work, at the moment it did it, so a slow document shows a pause
       rather than a stage advancing on a clock as though it had finished. */
    function narrate(poll, raw) {
        var N = window.AgentNarration;
        if (!N || !raw) { return; }
        var events = raw.events || [];
        for (var i = 0; i < events.length; i++) {
            var e = events[i];
            if (typeof e.seq === 'number' && e.seq > (poll.lastSeq || 0)) {
                poll.lastSeq = e.seq;
            }
            if (e.stage === 'pack_failed' || e.stage === 'file_failed') {
                N.fail(e.message);
            } else {
                N.step(e.message);
            }
        }
        N.progress(raw.files_done || 0, raw.files_total || 0);
    }

    /* One sentence for the chat once the panel goes away. */
    function summarise(raw) {
        if (raw.status === 'error') {
            return 'I could not finish that pack. ' + (raw.error_reason || '');
        }
        if (raw.status === 'needs_review') {
            return 'That pack is pre-filled and waiting for you. Open it to '
                 + 'confirm the flagged fields — nothing can be exported until '
                 + 'you have been through them.';
        }
        if (raw.status === 'reviewed') { return 'That pack is reviewed and ready to download.'; }
        return '';
    }

    async function pollOnce() {
        var poll = state.poll;
        if (!poll || poll.stopped) { return; }
        var packId = poll.packId;
        try {
            // `since` is the highest event seq already shown, so a poll that
            // lands after a reconnect resumes the narration instead of
            // repeating it.
            var raw = await api(API + '/' + encodeURIComponent(packId) + '/status'
                                + '?since=' + (poll.lastSeq || 0));
            poll.fails = 0;
            narrate(poll, raw);
            var status = normalisePack(raw || {});
            status.pack_id = status.pack_id || packId;
            renderStatus(status, null);

            if (status.status === 'processing') {
                poll.timer = setTimeout(pollOnce, POLL_MS);
                return;
            }
            // Left processing. One more fetch before stopping: the worker
            // emits its closing lines (why nothing could be drafted, the
            // eligibility reasons) after the poll that first sees a terminal
            // status, so stopping here dropped the most useful part of the
            // transcript.
            try {
                var tail = await api(API + '/' + encodeURIComponent(packId)
                                     + '/status?since=' + (poll.lastSeq || 0));
                narrate(poll, tail);
            } catch (tailErr) { /* the summary below still lands */ }

            // Closed AFTER the tail, not during it. narrate() used to call
            // end() the moment it saw a terminal status, which tore the panel
            // down before the closing lines had been fetched — so the most
            // useful part of the transcript (why nothing could be drafted, why
            // the bid was disqualified) rendered into nothing.
            if (window.AgentNarration) {
                window.AgentNarration.usage(status.usage || (raw && raw.usage));
                window.AgentNarration.end(summarise(status));
            }

            poll.stopped = true;
            state.poll = null;
            await loadPacks();
            renderStatus(status, null);
        } catch (e) {
            if (e.status === 401) { stopPolling(); return; }
            poll.fails += 1;
            renderStatus(null, e.message);
            if (poll.fails >= POLL_MAX_FAILS) {
                poll.stopped = true;   // stop the loop, keep the CHECK NOW button
                return;
            }
            poll.timer = setTimeout(pollOnce, POLL_MS * (1 + poll.fails));
        }
    }

    /* A pack must never look stuck. Every branch here says what is happening,
       how far it has got, how long it has been, and offers a way to ask again. */
    function renderStatus(status, errorMessage) {
        var host = $('apStatus');
        if (!host) { return; }
        var poll = state.poll;
        clear(host);
        show(host, true);

        var head = el('div', 'ap-status-head');
        head.appendChild(el('span', 'category-label', 'CURRENT PACK'));

        var pill;
        if (errorMessage) { pill = el('span', 'ap-pill is-error', 'STATUS UNKNOWN'); }
        else { pill = statusPill(status || { status: 'processing' }); }
        head.appendChild(pill);
        host.appendChild(head);

        if (errorMessage) {
            host.appendChild(el('p', 'ap-status-line is-bad',
                'The status check failed: ' + errorMessage +
                ' The pack keeps its place — this is only our view of it.'));
        }

        if (status) {
            var done = status.files_done || 0;
            var total = status.files_total || 0;

            var meter = el('div', 'ap-meter');
            var fill = el('div', 'ap-meter-fill');
            var pct = total > 0 ? Math.round((done / total) * 100) : 0;
            fill.style.width = pct + '%';
            if (status.status === 'processing') { meter.classList.add('is-pulsing'); }
            if (status.status === 'error') { fill.classList.add('is-error'); }
            meter.appendChild(fill);
            host.appendChild(meter);

            host.appendChild(el('p', 'ap-status-line',
                total > 0
                    ? (done + ' of ' + total + ' ' + plural(total, 'document', 'documents')
                       + ' processed.')
                    : 'Counting the documents in this pack…'));

            if (status.status === 'processing') {
                var elapsed = poll ? Date.now() - poll.started : 0;
                if (elapsed > SLOW_AFTER_MS) {
                    host.appendChild(el('p', 'ap-status-line is-warn',
                        'Still running after ' + Math.round(elapsed / 60000) +
                        ' minutes. You can leave this page — the pack keeps '
                        + 'processing without it.'));
                }
            } else if (status.status === 'needs_review') {
                host.appendChild(el('p', 'ap-status-line',
                    'Pre-filling finished. Every field the agent would not answer '
                    + 'is waiting for you below.'));
            } else if (status.status === 'reviewed') {
                host.appendChild(el('p', 'ap-status-line',
                    'Every flagged field on this pack has been acknowledged. It is '
                    + 'still a draft.'));
            }

            // error_reason is surfaced whatever the status says, because a pack
            // that failed halfway can report both a count and a reason.
            if (status.error_reason) {
                host.appendChild(el('p', 'ap-status-line is-bad',
                    'Reason: ' + status.error_reason));
            } else if (status.status === 'error') {
                host.appendChild(el('p', 'ap-status-line is-bad',
                    'The server reported an error but gave no reason.'));
            }
        }

        var actions = el('div', 'ap-status-actions');

        var check = el('button', 'ap-link-btn', 'CHECK NOW');
        check.type = 'button';
        check.addEventListener('click', function () {
            var id = (state.poll && state.poll.packId) || (status && status.pack_id);
            if (id) { startPolling(id); }
        });
        actions.appendChild(check);

        if (status && (status.status === 'needs_review' || status.status === 'reviewed')) {
            var open = el('button', 'ap-link-btn is-primary', 'OPEN REVIEW');
            open.type = 'button';
            open.addEventListener('click', function () { openReview(status.pack_id); });
            actions.appendChild(open);
        }

        var dismiss = el('button', 'ap-link-btn', 'HIDE');
        dismiss.type = 'button';
        dismiss.addEventListener('click', function () { stopPolling(); show(host, false); });
        actions.appendChild(dismiss);

        host.appendChild(actions);
    }

    /* --------------------------------------------------------------- history */

    async function loadPacks() {
        var host = $('apHistory');
        try {
            var raw = await api(API);
            var list = Array.isArray(raw) ? raw : asList(pick(raw || {}, ['packs'], []));
            state.packs = list.map(normalisePack);
            state.loaded = true;
            renderHistory();
        } catch (e) {
            state.loaded = true;
            if (host) {
                clear(host);
                host.appendChild(el('p', 'ap-msg is-bad',
                    'Could not load your packs: ' + e.message));
                var retry = el('button', 'ap-link-btn', 'TRY AGAIN');
                retry.type = 'button';
                retry.addEventListener('click', loadPacks);
                host.appendChild(retry);
            }
            show($('apEmpty'), false);
        }
    }

    function renderHistory() {
        var host = $('apHistory');
        var empty = $('apEmpty');
        if (!host) { return; }
        clear(host);

        if (!state.packs.length) {
            show(empty, true);
            return;
        }
        show(empty, false);

        var wrap = el('div', 'ap-table-wrap');
        var table = el('table', 'ap-table');

        var thead = el('thead');
        var hrow = el('tr');
        ['Pack Name', 'Files', 'Status', 'Submitted', 'Actions'].forEach(function (h) {
            var th = el('th', null, h);
            hrow.appendChild(th);
        });
        thead.appendChild(hrow);
        table.appendChild(thead);

        var tbody = el('tbody');
        state.packs.forEach(function (pack) {
            tbody.appendChild(historyRow(pack));
        });
        table.appendChild(tbody);

        wrap.appendChild(table);
        host.appendChild(wrap);
    }

    function historyRow(pack) {
        var meta = statusMeta(pack.status);
        var row = el('tr', 'ap-row ' + meta.cls);

        row.appendChild(el('td', 'ap-cell-name', pack.pack_name));
        row.appendChild(el('td', 'ap-cell-count',
            String(pack.file_count || pack.files_total || 0)));

        var statusCell = el('td', 'ap-cell-status');
        statusCell.appendChild(statusPill(pack));
        if (pack.status === 'processing' && pack.files_total) {
            statusCell.appendChild(el('span', 'ap-cell-sub',
                pack.files_done + '/' + pack.files_total));
        }
        if (pack.status === 'error' && pack.error_reason) {
            statusCell.appendChild(el('span', 'ap-cell-sub is-bad', pack.error_reason));
        }
        if (pack.status === 'reviewed') {
            statusCell.appendChild(el('span', 'ap-cell-sub',
                'draft — signatures and prices still blank'));
        }
        row.appendChild(statusCell);

        row.appendChild(el('td', 'ap-cell-date', formatDate(pack.submitted_at)));

        var actions = el('td', 'ap-cell-actions');
        if (pack.status === 'processing') {
            var watch = el('button', 'ap-link-btn', 'WATCH');
            watch.type = 'button';
            watch.addEventListener('click', function () { startPolling(pack.pack_id); });
            actions.appendChild(watch);
        } else if (pack.status === 'error') {
            var again = el('button', 'ap-link-btn', 'SUBMIT AGAIN');
            again.type = 'button';
            again.addEventListener('click', async function () {
                again.disabled = true;
                try {
                    await api(API + '/' + encodeURIComponent(pack.pack_id) + '/submit',
                              { method: 'POST' });
                    startPolling(pack.pack_id);
                } catch (e) {
                    again.disabled = false;
                    renderStatus(null, e.message);
                }
            });
            actions.appendChild(again);
        } else {
            var review = el('button', 'ap-link-btn is-primary',
                            pack.status === 'reviewed' ? 'OPEN DRAFT' : 'REVIEW FLAGS');
            review.type = 'button';
            review.addEventListener('click', function () { openReview(pack.pack_id); });
            actions.appendChild(review);
        }
        row.appendChild(actions);

        return row;
    }

    /* ---------------------------------------------------------- review drawer */

    function closeReview() {
        var panel = $('apReview');
        if (panel) { panel.classList.remove('active'); }
        state.detail = null;
    }

    async function openReview(packId) {
        if (!packId) { return; }
        var panel = $('apReview');
        var body = $('apReviewBody');
        if (!panel || !body) { return; }
        panel.classList.add('active');
        clear(body);
        body.appendChild(el('p', 'ap-msg', 'Loading the flagged fields…'));
        try {
            var raw = await api(API + '/' + encodeURIComponent(packId));
            state.detail = normaliseDetail(raw);
            state.detail.pack_id = state.detail.pack_id || packId;
            renderReview();
        } catch (e) {
            clear(body);
            body.appendChild(el('p', 'ap-msg is-bad',
                'Could not load this pack: ' + e.message));
        }
    }

    function renderReview() {
        var body = $('apReviewBody');
        var title = $('apReviewTitle');
        var detail = state.detail;
        if (!body || !detail) { return; }
        clear(body);

        if (title) { title.textContent = detail.pack_name; }

        var totals = reviewState(detail);

        // The standing reminder. It is the first thing in the drawer and it
        // does not change with the review's progress, because the thing it
        // says is not a stage the pack passes through.
        var banner = el('div', 'ap-draft-banner');
        banner.appendChild(el('strong', null, 'DRAFT'));
        banner.appendChild(el('span', null,
            'Nothing in this pack is signed, priced or declared on your behalf. '
            + 'Acknowledging a field records that a person looked at it — it '
            + 'does not fill it in.'));
        body.appendChild(banner);

        // Progress. A half-reviewed pack reads differently from a finished one
        // at a glance, not only when you try to export.
        var progress = el('div', 'ap-progress' + (totals.open === 0 ? ' is-done' : ''));
        progress.appendChild(el('span', 'ap-progress-count',
            totals.acked + ' of ' + totals.flags + ' flagged '
            + plural(totals.flags, 'field', 'fields') + ' acknowledged'));
        var pmeter = el('div', 'ap-meter');
        var pfill = el('div', 'ap-meter-fill');
        pfill.style.width = (totals.flags ? Math.round((totals.acked / totals.flags) * 100) : 0)
                            + '%';
        pmeter.appendChild(pfill);
        progress.appendChild(pmeter);
        body.appendChild(progress);

        if (!detail.documents.length) {
            body.appendChild(el('p', 'ap-msg',
                'No flagged fields were reported for this pack yet.'));
        }

        detail.documents.forEach(function (doc) {
            body.appendChild(renderDocument(doc));
        });

        body.appendChild(renderExport(totals));
    }

    function renderDocument(doc) {
        var section = el('section', 'ap-doc');

        var head = el('div', 'ap-doc-head');
        head.appendChild(el('h4', 'ap-doc-name', doc.document));
        var open = doc.items.filter(function (i) { return !i.acknowledged; }).length;
        head.appendChild(el('span', 'ap-doc-count',
            open === 0
                ? (doc.items.length + ' ' + plural(doc.items.length, 'field', 'fields')
                   + ' · all acknowledged')
                : (open + ' of ' + doc.items.length + ' still to acknowledge')));
        section.appendChild(head);

        doc.items.forEach(function (item) {
            section.appendChild(renderItem(doc, item));
        });

        if (doc.fills.length) {
            section.appendChild(renderValues(doc));
        }

        return section;
    }

    function renderItem(doc, item) {
        var card = el('div', 'ap-item' + (item.acknowledged ? ' is-acked' : ''));

        var top = el('div', 'ap-item-top');
        top.appendChild(el('span', 'ap-item-label', item.label));
        if (item.category) {
            top.appendChild(el('span', 'ap-chip', item.category));
        }
        card.appendChild(top);

        var meta = el('div', 'ap-item-meta');
        meta.appendChild(el('code', 'ap-item-key', item.item_key));
        if (item.location) { meta.appendChild(el('span', null, item.location)); }
        card.appendChild(meta);

        if (item.reason) {
            card.appendChild(el('p', 'ap-item-reason', item.reason));
        }

        if (item.acknowledged) {
            var done = el('div', 'ap-item-done');
            done.appendChild(el('span', 'ap-tick', '✓'));
            done.appendChild(el('span', 'ap-item-note', item.note || 'Acknowledged.'));
            card.appendChild(done);
            return card;
        }

        var form = el('div', 'ap-ack');
        var input = el('input', 'ap-input ap-ack-note');
        input.type = 'text';
        input.maxLength = 300;
        input.autocomplete = 'off';
        input.placeholder = 'What did you check about this field?';
        input.setAttribute('aria-label', 'Note for ' + item.label);

        var button = el('button', 'ap-ack-btn', 'ACKNOWLEDGE');
        button.type = 'button';
        button.disabled = true;

        var hint = el('p', 'ap-ack-hint',
            'One field, one note. A blanket confirmation is refused.');

        function validate() {
            var problem = noteProblem(input.value);
            button.disabled = Boolean(problem);
            hint.textContent = problem ||
                'Recorded against this field only.';
            hint.classList.toggle('is-bad', Boolean(problem) && input.value.trim().length > 0);
        }
        input.addEventListener('input', validate);

        button.addEventListener('click', async function () {
            var problem = noteProblem(input.value);
            if (problem) { hint.textContent = problem; return; }
            button.disabled = true;
            button.textContent = 'SAVING…';
            try {
                await sendJson(API + '/' + encodeURIComponent(state.detail.pack_id)
                               + '/acknowledge', 'POST', {
                    review_id: doc.review_id,
                    item_key: item.item_key,
                    note: input.value.trim()
                });
                item.acknowledged = true;
                item.note = input.value.trim();
                renderReview();
                loadPacks();
            } catch (e) {
                button.disabled = false;
                button.textContent = 'ACKNOWLEDGE';
                hint.textContent = 'Not recorded: ' + e.message;
                hint.classList.add('is-bad');
            }
        });

        form.appendChild(input);
        form.appendChild(button);
        card.appendChild(form);
        card.appendChild(hint);
        return card;
    }

    /* What the agent DID write. Acknowledging only the fields it skipped would
       leave these unread, so they are confirmed as a set — and until they are,
       export is held. */
    function renderValues(doc) {
        var box = el('div', 'ap-values' + (doc.values_confirmed ? ' is-confirmed' : ''));
        box.appendChild(el('h5', 'ap-values-head',
            'Values the agent filled in (' + doc.fills.length + ')'));

        var checks = [];
        doc.fills.forEach(function (fill) {
            var row = el('label', 'ap-value-row');
            var box2 = document.createElement('input');
            box2.type = 'checkbox';
            box2.className = 'ap-value-check';
            box2.checked = doc.values_confirmed;
            box2.disabled = doc.values_confirmed;
            checks.push({ input: box2, key: fill.item_key });
            row.appendChild(box2);

            var text = el('span', 'ap-value-text');
            text.appendChild(el('span', 'ap-value-label', fill.label));
            text.appendChild(el('span', 'ap-value-value', fill.value));
            row.appendChild(text);

            box.appendChild(row);
        });

        if (doc.values_confirmed) {
            box.appendChild(el('p', 'ap-values-note is-ok',
                'These values have been confirmed.'));
            return box;
        }

        var hint = el('p', 'ap-values-note',
            'Tick each value you have read, then confirm. Export is held until '
            + 'they are confirmed.');
        var confirm = el('button', 'ap-ack-btn', 'CONFIRM THESE VALUES');
        confirm.type = 'button';

        function refreshConfirm() {
            var picked = checks.filter(function (c) { return c.input.checked; }).length;
            confirm.disabled = picked !== checks.length;
            confirm.textContent = picked === checks.length
                ? 'CONFIRM THESE VALUES'
                : 'CONFIRM (' + picked + '/' + checks.length + ' ticked)';
        }
        checks.forEach(function (c) {
            c.input.addEventListener('change', refreshConfirm);
        });
        refreshConfirm();

        confirm.addEventListener('click', async function () {
            confirm.disabled = true;
            confirm.textContent = 'SAVING…';
            try {
                await sendJson(API + '/' + encodeURIComponent(state.detail.pack_id)
                               + '/confirm-values', 'POST', {
                    review_id: doc.review_id,
                    confirmed_keys: checks.map(function (c) { return c.key; })
                });
                doc.values_confirmed = true;
                renderReview();
            } catch (e) {
                confirm.disabled = false;
                confirm.textContent = 'CONFIRM THESE VALUES';
                hint.textContent = 'Not confirmed: ' + e.message;
                hint.classList.add('is-bad');
            }
        });

        box.appendChild(hint);
        box.appendChild(confirm);
        return box;
    }

    function renderExport(totals) {
        var box = el('div', 'ap-export' + (totals.exportable ? ' is-open' : ' is-blocked'));
        box.appendChild(el('span', 'category-label', 'EXPORT'));

        var button = el('button', 'btn-gold ap-export-btn', 'EXPORT REVIEWED DRAFT');
        button.type = 'button';
        button.disabled = !totals.exportable;

        var why = el('p', 'ap-export-why');

        if (!totals.exportable) {
            var parts = [];
            if (totals.open > 0) {
                parts.push(totals.open + ' of ' + totals.flags + ' flagged '
                           + plural(totals.flags, 'field', 'fields')
                           + ' not acknowledged yet');
            }
            if (totals.valuesPending > 0) {
                parts.push(totals.valuesPending + ' '
                           + plural(totals.valuesPending, 'document', 'documents')
                           + ' with auto-filled values still unconfirmed');
            }
            if (!parts.length) {
                parts.push('this pack has no reviewed documents yet');
            }
            why.textContent = 'Export is blocked — ' + parts.join(', and ') + '.';
            why.classList.add('is-bad');

            if (totals.byDoc.length) {
                var ul = el('ul', 'ap-export-list');
                totals.byDoc.forEach(function (d) {
                    var bits = [];
                    if (d.open) {
                        bits.push(d.open + ' ' + plural(d.open, 'field', 'fields')
                                  + ' to acknowledge');
                    }
                    if (d.valuesPending) { bits.push('values not confirmed'); }
                    ul.appendChild(el('li', null, d.document + ' — ' + bits.join(', ')));
                });
                box.appendChild(button);
                box.appendChild(why);
                box.appendChild(ul);
                return box;
            }
        } else {
            why.textContent = 'Every flagged field has been acknowledged. The export '
                + 'is a stamped DRAFT: the blank signature, price and declaration '
                + 'fields are still blank, and still yours to fill in.';
        }

        var result = el('div', 'ap-export-result');

        button.addEventListener('click', async function () {
            button.disabled = true;
            button.textContent = 'PREPARING…';
            clear(result);
            try {
                var out = await api(API + '/' + encodeURIComponent(state.detail.pack_id)
                                    + '/export', { method: 'POST' });
                var url = safeUrl(pick(out || {}, ['download_url', 'url'], ''));
                button.textContent = 'EXPORT REVIEWED DRAFT';
                button.disabled = false;
                if (!url) {
                    result.appendChild(el('p', 'ap-msg is-bad',
                        'The export came back without a usable download link.'));
                    return;
                }
                var link = el('a', 'ap-download', 'DOWNLOAD THE DRAFT');
                link.setAttribute('href', url);
                link.setAttribute('rel', 'noopener noreferrer');
                link.setAttribute('download', '');
                result.appendChild(link);
                result.appendChild(el('p', 'ap-msg',
                    'The file is stamped as a reviewed draft. It is not a submission.'));
                loadPacks();
            } catch (e) {
                button.textContent = 'EXPORT REVIEWED DRAFT';
                button.disabled = false;
                result.appendChild(el('p', 'ap-msg is-bad', 'Export refused: ' + e.message));
            }
        });

        box.appendChild(button);
        box.appendChild(why);
        box.appendChild(result);
        return box;
    }

    /* ------------------------------------------------------------------ wiring */

    function wire() {
        var zone = $('apDropZone');
        var input = $('apFileInput');
        if (zone && input && zone.dataset.wired !== '1') {
            zone.dataset.wired = '1';
            // The vault's drop-zone wiring, shared rather than reimplemented.
            if (typeof global.bindDropZone === 'function') {
                global.bindDropZone(zone, input, addFiles);
            } else {
                input.addEventListener('change', function () { addFiles(input.files); });
            }
        }

        var newBtn = $('apNewPackBtn');
        if (newBtn) { newBtn.addEventListener('click', newPack); }

        var nameInput = $('apPackName');
        if (nameInput) {
            nameInput.addEventListener('input', scheduleSaveName);
            nameInput.addEventListener('blur', function () {
                if (nameTimer) { clearTimeout(nameTimer); nameTimer = null; }
                if (state.draft) { saveName(nameInput.value); }
            });
        }

        var submit = $('apSubmitBtn');
        if (submit) { submit.addEventListener('click', submitPack); }

        var discard = $('apDiscardBtn');
        if (discard) { discard.addEventListener('click', discardDraft); }

        var close = $('apReviewClose');
        if (close) { close.addEventListener('click', closeReview); }

        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') { return; }
            var panel = $('apReview');
            if (panel && panel.classList.contains('active')) { closeReview(); }
        });
    }

    /* Called when the tab is opened. The list is fetched once, then refreshed
       on each visit so a pack that finished elsewhere is not stale. */
    function activate() {
        loadPacks().then(function () {
            // Resume watching anything the server still calls processing, so a
            // reload mid-run does not leave a pack apparently frozen.
            var running = state.packs.filter(function (p) {
                return p.status === 'processing';
            });
            if (running.length && !state.poll) { startPolling(running[0].pack_id); }
        });
    }

    global.AutofillPacks = {
        activate: activate,
        openReview: openReview,
        // exposed for verification from the console
        _state: state
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }
})(window);
