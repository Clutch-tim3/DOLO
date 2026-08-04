/*
 * Compliance Vault uploader — shared by static/vault.html and the
 * #tab-archive panel in workspace.html.
 *
 * The vault previously had no way to add a document. workspace.html carried a
 * handleArchiveFileUpload() and a triggerBlockUpload(), but nothing called
 * them and the file input they clicked did not exist — they were orphaned when
 * the vault was redesigned. This lives in one file so the two renderings
 * cannot drift apart the way their markup did.
 */
(function (global) {
    'use strict';

    var ACCEPTED = ['.pdf', '.docx', '.doc'];

    function getCompanyId() {
        if (typeof global.getCompanyId === 'function') {
            try { return global.getCompanyId(); } catch (e) { /* fall through */ }
        }
        return localStorage.getItem('cairoai_company_id') || 'pro_corp';
    }

    function hasAcceptedExtension(name) {
        var lower = (name || '').toLowerCase();
        return ACCEPTED.some(function (ext) { return lower.endsWith(ext); });
    }

    /* A dropped file that misses the zone would otherwise be opened by the
       browser, navigating away from the page and losing any unsaved state. */
    var windowGuardArmed = false;
    function armWindowGuard() {
        if (windowGuardArmed) return;
        windowGuardArmed = true;
        ['dragover', 'drop'].forEach(function (evt) {
            global.addEventListener(evt, function (e) { e.preventDefault(); });
        });
    }

    function renderQueueItem(queueEl, file) {
        var row = document.createElement('div');
        row.className = 'vault-upload-item';
        var name = document.createElement('span');
        name.className = 'name';
        name.textContent = file.name;
        var state = document.createElement('span');
        state.className = 'state';
        state.textContent = 'Uploading';
        row.appendChild(name);
        row.appendChild(state);
        queueEl.appendChild(row);
        return { row: row, state: state };
    }

    function settle(entry, cls, text) {
        entry.row.classList.add(cls);
        entry.state.textContent = text;
    }

    /**
     * @param {Object} opts
     *   zone      - the drop zone element
     *   input     - the <input type=file>
     *   queue     - container for per-file progress rows
     *   onDone    - called once after a batch, with a summary
     *   notify    - optional (title, message) toast
     */
    function initVaultUploader(opts) {
        var zone = opts.zone, input = opts.input, queue = opts.queue;
        if (!zone || !input) return;
        armWindowGuard();

        function announce(title, message) {
            if (typeof opts.notify === 'function') opts.notify(title, message);
        }

        async function uploadOne(file) {
            var entry = queue ? renderQueueItem(queue, file) : null;

            if (!hasAcceptedExtension(file.name)) {
                if (entry) settle(entry, 'is-bad', 'Not a document');
                return { ok: false, file: file, reason: 'unsupported' };
            }

            var form = new FormData();
            form.append('file', file);
            // The server classifies the document and re-files it if this guess
            // is wrong, so the default is only a starting point.
            form.append('target_block', opts.targetBlock || 'CSD_CERT');

            try {
                var res = await fetch('/api/archive/upload-document', {
                    method: 'POST',
                    headers: { 'X-Company-ID': getCompanyId() },
                    body: form
                });
                if (!res.ok) {
                    var detail = '';
                    try { detail = (await res.json()).detail || ''; } catch (e) { /* no body */ }
                    if (entry) settle(entry, 'is-bad', res.status === 415 ? 'Wrong type' : 'Failed');
                    return { ok: false, file: file, reason: detail || ('HTTP ' + res.status) };
                }
                var data = await res.json();
                if (entry) {
                    if (data.auto_sorted) settle(entry, 'is-moved', 'Filed as ' + (data.detected_label || data.actual_block));
                    else settle(entry, 'is-ok', 'Filed');
                }
                return { ok: true, file: file, data: data };
            } catch (e) {
                if (entry) settle(entry, 'is-bad', 'Failed');
                return { ok: false, file: file, reason: 'network' };
            }
        }

        async function handleFiles(fileList) {
            var files = Array.prototype.slice.call(fileList || []);
            if (!files.length) return;

            zone.classList.add('is-busy');
            if (queue) queue.innerHTML = '';

            var results = [];
            // Sequential rather than parallel: each upload runs text extraction
            // and classification server-side, and a phone dropping ten files at
            // once should not open ten of those at the same time.
            for (var i = 0; i < files.length; i++) {
                results.push(await uploadOne(files[i]));
            }

            zone.classList.remove('is-busy');
            input.value = '';   // let the same file be picked again

            var filed = results.filter(function (r) { return r.ok; });
            var moved = filed.filter(function (r) { return r.data && r.data.auto_sorted; });
            var failed = results.filter(function (r) { return !r.ok; });

            if (filed.length) {
                var msg = filed.length + (filed.length === 1 ? ' document filed' : ' documents filed');
                if (moved.length) {
                    msg += ' — ' + moved.length + ' auto-sorted into the correct block';
                }
                announce('Vault updated', msg + '.');
            }
            if (failed.length && !filed.length) {
                announce('Nothing filed', failed.length + ' file(s) could not be added. PDF and Word only.');
            }

            if (typeof opts.onDone === 'function') {
                opts.onDone({ filed: filed.length, moved: moved.length, failed: failed.length });
            }
        }

        zone.addEventListener('click', function (e) {
            // Let a real control inside the zone do its own thing.
            if (e.target.closest('a')) return;
            input.click();
        });
        zone.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
        });
        input.addEventListener('change', function () { handleFiles(input.files); });

        // dragenter/dragleave fire for every child element, so a plain
        // add/remove flickers as the pointer crosses the icon or the text. The
        // counter keeps the highlight stable until the pointer truly leaves.
        var depth = 0;
        zone.addEventListener('dragenter', function (e) {
            e.preventDefault();
            depth += 1;
            zone.classList.add('is-dragging');
        });
        zone.addEventListener('dragover', function (e) {
            e.preventDefault();                       // required, or drop never fires
            e.dataTransfer.dropEffect = 'copy';
        });
        zone.addEventListener('dragleave', function () {
            depth = Math.max(0, depth - 1);
            if (depth === 0) zone.classList.remove('is-dragging');
        });
        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            depth = 0;
            zone.classList.remove('is-dragging');
            handleFiles(e.dataTransfer && e.dataTransfer.files);
        });
    }

    global.initVaultUploader = initVaultUploader;
})(window);
