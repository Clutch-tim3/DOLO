/* ==========================================================================
   CairoAI — Company Profile questionnaire wizard
   --------------------------------------------------------------------------
   Feeds Agent Autofill. Whatever is saved here is what CairoAI writes into
   real SBD forms, so the wizard is built around three rules:

   1. The form definition is fetched from /api/questionnaire/definition. It is
      NOT duplicated here. A hardcoded copy would drift from
      agent_autofill/templates/questionnaire_schema.py the first time a field
      changed, and the drift would be invisible until a draft came out wrong.
      If the endpoint is not mounted, the page says so rather than guessing.

   2. Nothing saves without an explicit confirmation. The user sees a literal
      before/after diff from /api/questionnaire/preview and must tick the
      confirm box; only then does the save request carry confirmed:true. The
      server refuses the write regardless if the flag is absent, so this is the
      UI half of a gate that is enforced on both sides.

   3. All server-derived text is written with textContent, never innerHTML,
      matching the rest of the app. Stored company values are user-supplied
      strings and are never parsed as markup.
   ========================================================================== */

(function () {
    'use strict';

    var DEF = null;          // questionnaire definition from the server
    var ANSWERS = {};        // current working answers
    var DIRECTORS = [];      // working copy of the directors array
    var STEP = 0;            // index into DEF.steps, or DEF.steps.length for review
    var LAST_ERRORS = {};

    var el = {
        banner: null,
        progress: null,
        stepLabel: null,
        steps: null
    };

    // ---------------------------------------------------------------- utils

    /* The company comes from the session cookie. This used to read a
       localStorage key and send it as X-Company-ID, which the server trusted —
       so the wizard could read and overwrite any company's profile, and that
       profile is what Agent Autofill later writes into real SBD forms. */
    function headers() {
        return { 'Content-Type': 'application/json' };
    }

    /* Every request here goes through this so the cookie is always attached
       and a lapsed session puts the sign-in gate back rather than surfacing as
       an unexplained wizard error. */
    async function request(url, options) {
        var opts = options || {};
        opts.credentials = 'same-origin';
        var res = await fetch(url, opts);
        if (res.status === 401 && window.CairoAuth) {
            window.CairoAuth.handleUnauthorized();
        }
        return res;
    }

    function node(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null) { n.textContent = String(text); }
        return n;
    }

    function clear(n) {
        while (n.firstChild) { n.removeChild(n.firstChild); }
    }

    function banner(message, kind) {
        el.banner.className = 'q-banner show ' + (kind || '');
        el.banner.textContent = message;
    }

    function hideBanner() {
        el.banner.className = 'q-banner';
        el.banner.textContent = '';
    }

    function displayValue(v) {
        if (v === null || v === undefined || v === '') { return '(not set)'; }
        if (Array.isArray(v)) {
            if (!v.length) { return '(none)'; }
            return v.map(function (d) {
                if (d && typeof d === 'object') {
                    return d.name + ' — ' + d.id_number + ' — ' +
                        (d.is_state_employee ? 'in the service of the state' : 'not in the service of the state');
                }
                return String(d);
            }).join(' | ');
        }
        return String(v);
    }

    // ------------------------------------------------------------- rendering

    function renderProgress() {
        clear(el.progress);
        var total = DEF.steps.length + 1; // + review
        for (var i = 0; i < total; i++) {
            var pip = node('div', 'q-pip');
            if (i < STEP) { pip.className = 'q-pip done'; }
            else if (i === STEP) { pip.className = 'q-pip current'; }
            el.progress.appendChild(pip);
        }
        var label = (STEP < DEF.steps.length)
            ? 'Step ' + (STEP + 1) + ' of ' + total + ' — ' + DEF.steps[STEP].title
            : 'Step ' + total + ' of ' + total + ' — Review and confirm';
        el.stepLabel.textContent = label;
    }

    function fieldControl(f) {
        var input;
        if (f.type === 'textarea') {
            input = document.createElement('textarea');
        } else if (f.type === 'select') {
            input = document.createElement('select');
            var blank = document.createElement('option');
            blank.value = '';
            blank.textContent = f.required ? 'Select…' : '(leave blank)';
            input.appendChild(blank);
            (f.options || []).forEach(function (opt) {
                var o = document.createElement('option');
                o.value = opt;
                o.textContent = opt;
                input.appendChild(o);
            });
        } else {
            input = document.createElement('input');
            input.type = (f.type === 'email' || f.type === 'tel') ? f.type : 'text';
        }
        input.id = 'q_' + f.key;
        input.name = f.key;
        if (f.placeholder) { input.placeholder = f.placeholder; }
        var current = ANSWERS[f.key];
        input.value = (current === null || current === undefined) ? '' : String(current);
        input.addEventListener('input', function () { ANSWERS[f.key] = input.value; });
        input.addEventListener('change', function () { ANSWERS[f.key] = input.value; });
        return input;
    }

    function renderField(f) {
        var group = node('div', 'form-group');
        group.setAttribute('data-field', f.key);

        var label = node('label', null, f.label);
        label.setAttribute('for', 'q_' + f.key);
        if (f.required) {
            label.appendChild(node('span', 'q-req', '*'));
        }
        group.appendChild(label);

        if (f.type === 'director_list') {
            group.appendChild(renderDirectors(f));
        } else {
            group.appendChild(fieldControl(f));
        }

        if (f.help_text) { group.appendChild(node('div', 'q-help', f.help_text)); }
        if (f.legal_note) { group.appendChild(node('div', 'q-legal', f.legal_note)); }

        var err = node('div', 'q-error');
        err.setAttribute('data-error-for', f.key);
        group.appendChild(err);

        return group;
    }

    function renderDirectors() {
        var wrap = node('div');
        wrap.id = 'qDirectors';

        function repaint() {
            clear(wrap);
            DIRECTORS.forEach(function (d, idx) {
                wrap.appendChild(directorCard(d, idx, repaint));
            });
            var add = node('button', 'q-btn ghost', '+ Add director');
            add.type = 'button';
            add.addEventListener('click', function () {
                // is_state_employee starts as null, not false. An unanswered
                // sworn declaration must fail validation, not default to "no".
                DIRECTORS.push({ name: '', id_number: '', is_state_employee: null });
                repaint();
            });
            wrap.appendChild(add);
            ANSWERS.directors = DIRECTORS;
        }

        if (!DIRECTORS.length) {
            DIRECTORS.push({ name: '', id_number: '', is_state_employee: null });
        }
        repaint();
        return wrap;
    }

    function directorCard(d, idx, repaint) {
        var card = node('div', 'q-director');

        var head = node('div', 'q-director-head');
        head.appendChild(node('span', null, 'Director ' + (idx + 1)));
        if (DIRECTORS.length > 1) {
            var rm = node('button', 'q-btn ghost', 'Remove');
            rm.type = 'button';
            rm.style.padding = '4px 10px';
            rm.addEventListener('click', function () {
                DIRECTORS.splice(idx, 1);
                repaint();
            });
            head.appendChild(rm);
        }
        card.appendChild(head);

        var grid = node('div', 'q-dgrid');

        var nameGroup = node('div', 'form-group');
        nameGroup.appendChild(node('label', null, 'Full name'));
        var nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.value = d.name || '';
        nameInput.placeholder = 'As per CIPC records';
        nameInput.addEventListener('input', function () { d.name = nameInput.value; });
        nameGroup.appendChild(nameInput);
        var nameErr = node('div', 'q-error');
        nameErr.setAttribute('data-error-for', 'directors[' + idx + '].name');
        nameGroup.appendChild(nameErr);
        grid.appendChild(nameGroup);

        var idGroup = node('div', 'form-group');
        idGroup.appendChild(node('label', null, 'Identity number'));
        var idInput = document.createElement('input');
        idInput.type = 'text';
        idInput.value = d.id_number || '';
        idInput.placeholder = '13-digit SA ID, or passport number';
        idInput.addEventListener('input', function () { d.id_number = idInput.value; });
        idGroup.appendChild(idInput);
        var idErr = node('div', 'q-error');
        idErr.setAttribute('data-error-for', 'directors[' + idx + '].id_number');
        idGroup.appendChild(idErr);
        grid.appendChild(idGroup);

        card.appendChild(grid);

        var stateBox = node('div', 'q-statebox form-group');
        stateBox.appendChild(node('label', null, 'Is this director in the service of the state?'));
        stateBox.appendChild(node(
            'div', 'q-help',
            'SBD 4 declaration of interest. Answer it yourself — CairoAI will not assume it, ' +
            'and a false declaration can have the bid rejected.'
        ));

        var row = node('div', 'q-radio-row');
        [['Yes', true], ['No', false]].forEach(function (pair) {
            var lbl = node('label');
            var radio = document.createElement('input');
            radio.type = 'radio';
            radio.name = 'state_emp_' + idx;
            radio.checked = (d.is_state_employee === pair[1]);
            radio.addEventListener('change', function () {
                if (radio.checked) { d.is_state_employee = pair[1]; }
            });
            lbl.appendChild(radio);
            lbl.appendChild(node('span', null, pair[0]));
            row.appendChild(lbl);
        });
        stateBox.appendChild(row);

        var stateErr = node('div', 'q-error');
        stateErr.setAttribute('data-error-for', 'directors[' + idx + '].is_state_employee');
        stateBox.appendChild(stateErr);

        card.appendChild(stateBox);
        return card;
    }

    function renderStep() {
        clear(el.steps);
        renderProgress();

        if (STEP >= DEF.steps.length) {
            el.steps.appendChild(renderReview());
            return;
        }

        var step = DEF.steps[STEP];
        var wrap = node('div', 'q-step active');
        wrap.appendChild(node('h2', 'q-title', step.title));
        wrap.appendChild(node('p', 'q-blurb', step.blurb));

        var fields = node('div', 'q-fields');
        step.fields.forEach(function (f) { fields.appendChild(renderField(f)); });
        wrap.appendChild(fields);

        wrap.appendChild(actions({
            backLabel: STEP === 0 ? null : 'Back',
            nextLabel: (STEP === DEF.steps.length - 1) ? 'Review' : 'Next',
            onBack: function () { STEP -= 1; hideBanner(); renderStep(); window.scrollTo(0, 0); },
            onNext: function () { STEP += 1; hideBanner(); renderStep(); window.scrollTo(0, 0); }
        }));

        el.steps.appendChild(wrap);
        paintErrors();
    }

    function actions(opts) {
        var bar = node('div', 'q-actions');
        var left = node('div');
        if (opts.backLabel) {
            var back = node('button', 'q-btn', opts.backLabel);
            back.type = 'button';
            back.addEventListener('click', opts.onBack);
            left.appendChild(back);
        }
        bar.appendChild(left);

        var right = node('div');
        var next = node('button', 'q-btn primary', opts.nextLabel);
        next.type = 'button';
        next.id = opts.nextId || '';
        if (opts.nextDisabled) { next.disabled = true; }
        next.addEventListener('click', opts.onNext);
        right.appendChild(next);
        bar.appendChild(right);
        return bar;
    }

    function paintErrors() {
        var nodes = el.steps.querySelectorAll('[data-error-for]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-error-for');
            var msg = LAST_ERRORS[key];
            if (msg) {
                nodes[i].textContent = msg;
                nodes[i].className = 'q-error show';
                var grp = nodes[i].closest('.form-group');
                if (grp) { grp.classList.add('has-error'); }
            } else {
                nodes[i].textContent = '';
                nodes[i].className = 'q-error';
            }
        }
    }

    // ------------------------------------------------------------ review step

    function renderReview() {
        var wrap = node('div', 'q-step active');
        wrap.appendChild(node('h2', 'q-title', 'Review and confirm'));
        wrap.appendChild(node(
            'p', 'q-blurb',
            'Nothing has been saved yet. These are the exact changes CairoAI will store, ' +
            'and the exact values it will write into your tender drafts. Check them against ' +
            'your CIPC, SARS and CSD paperwork before confirming.'
        ));

        var diffHost = node('div');
        diffHost.id = 'qDiffHost';
        diffHost.appendChild(node('p', 'q-blurb', 'Checking…'));
        wrap.appendChild(diffHost);

        el.steps.appendChild(wrap);

        ANSWERS.directors = DIRECTORS;

        request('/api/questionnaire/preview', {
            method: 'POST',
            headers: headers(),
            body: JSON.stringify({ answers: ANSWERS })
        }).then(function (r) {
            return r.json().then(function (body) { return { ok: r.ok, body: body }; });
        }).then(function (res) {
            renderDiff(diffHost, wrap, res.body);
        }).catch(function (e) {
            clear(diffHost);
            diffHost.appendChild(node('p', 'q-blurb',
                'Could not reach the questionnaire API: ' + e.message));
        });

        return wrap;
    }

    function renderDiff(host, wrap, body) {
        clear(host);

        if (body.status === 'invalid') {
            LAST_ERRORS = body.errors || {};
            host.appendChild(node('p', 'q-blurb',
                'Some answers need fixing before anything can be saved:'));
            var list = node('div', 'q-diff');
            Object.keys(LAST_ERRORS).forEach(function (k) {
                var row = node('div', 'q-diff-row');
                row.appendChild(node('div', 'q-diff-field', k));
                var msg = node('div', 'q-diff-new', LAST_ERRORS[k]);
                msg.style.gridColumn = 'span 2';
                row.appendChild(msg);
                list.appendChild(row);
            });
            host.appendChild(list);
            host.appendChild(actions({
                backLabel: 'Back',
                nextLabel: 'Back to first step',
                onBack: function () { STEP -= 1; renderStep(); window.scrollTo(0, 0); },
                onNext: function () { STEP = 0; renderStep(); window.scrollTo(0, 0); }
            }));
            return;
        }

        if (body.status !== 'ok') {
            host.appendChild(node('p', 'q-blurb',
                body.message || 'The questionnaire API refused this input.'));
            return;
        }

        LAST_ERRORS = {};
        (body.warnings || []).forEach(function (w) {
            host.appendChild(node('div', 'q-legal', w));
        });

        var changes = body.changes || [];
        if (!changes.length) {
            host.appendChild(node('p', 'q-blurb',
                'Nothing has changed — the stored profile already matches these answers.'));
            host.appendChild(actions({
                backLabel: 'Back',
                nextLabel: 'Done',
                onBack: function () { STEP -= 1; renderStep(); window.scrollTo(0, 0); },
                onNext: function () { STEP = 0; renderStep(); window.scrollTo(0, 0); }
            }));
            return;
        }

        var table = node('div', 'q-diff');
        var head = node('div', 'q-diff-row head');
        head.appendChild(node('div', null, 'Field'));
        head.appendChild(node('div', null, 'Currently stored'));
        head.appendChild(node('div', null, 'Will become'));
        table.appendChild(head);

        changes.forEach(function (c) {
            var row = node('div', 'q-diff-row');
            row.appendChild(node('div', 'q-diff-field', c.field));
            row.appendChild(node('div', 'q-diff-old', displayValue(c.current)));
            row.appendChild(node('div', 'q-diff-new', displayValue(c.proposed)));
            table.appendChild(row);
        });
        host.appendChild(table);

        // The confirmation gate. The save request only carries confirmed:true
        // while this box is ticked; the server refuses the write otherwise.
        var box = node('div', 'q-confirmbox');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.id = 'qConfirm';
        var lbl = node('label', null,
            'I have checked these values against my company records and confirm they are ' +
            'correct. I understand CairoAI will use them to draft real tender submissions, ' +
            'and that I remain responsible for reviewing and signing every document myself.');
        lbl.setAttribute('for', 'qConfirm');
        box.appendChild(cb);
        box.appendChild(lbl);
        host.appendChild(box);

        var bar = actions({
            backLabel: 'Back',
            nextLabel: 'Save profile',
            nextId: 'qSaveBtn',
            nextDisabled: true,
            onBack: function () { STEP -= 1; renderStep(); window.scrollTo(0, 0); },
            onNext: function () { doSave(cb); }
        });
        host.appendChild(bar);

        var saveBtn = document.getElementById('qSaveBtn');
        cb.addEventListener('change', function () {
            if (saveBtn) { saveBtn.disabled = !cb.checked; }
        });
    }

    function doSave(cb) {
        if (!cb.checked) {
            banner('Tick the confirmation box first. Nothing is saved without it.', 'bad');
            return;
        }
        ANSWERS.directors = DIRECTORS;
        request('/api/questionnaire/save', {
            method: 'POST',
            headers: headers(),
            body: JSON.stringify({ answers: ANSWERS, confirmed: true })
        }).then(function (r) {
            return r.json().then(function (body) { return { status: r.status, body: body }; });
        }).then(function (res) {
            if (res.body.status === 'success') {
                banner('Saved. ' + (res.body.updated_fields || []).length +
                       ' field(s) updated on your company profile.', 'ok');
                STEP = 0;
                loadExisting().then(renderStep);
                window.scrollTo(0, 0);
            } else if (res.body.status === 'confirmation_required') {
                banner('The server did not receive a confirmation, so nothing was saved.', 'bad');
            } else {
                LAST_ERRORS = res.body.errors || {};
                banner(res.body.message || 'Save refused. Check the highlighted fields.', 'bad');
                paintErrors();
            }
        }).catch(function (e) {
            banner('Could not reach the questionnaire API: ' + e.message, 'bad');
        });
    }

    // ------------------------------------------------------------------ boot

    function loadExisting() {
        return request('/api/questionnaire', { headers: headers() })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { return; }
                ANSWERS = data.answers || {};
                DIRECTORS = Array.isArray(ANSWERS.directors) ? ANSWERS.directors.slice() : [];
            })
            .catch(function () { /* fall through to an empty form */ });
    }

    function init() {
        el.banner = document.getElementById('qBanner');
        el.progress = document.getElementById('qProgress');
        el.stepLabel = document.getElementById('qStepLabel');
        el.steps = document.getElementById('qSteps');
        if (!el.steps) { return; }

        request('/api/questionnaire/definition', { headers: headers() })
            .then(function (r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(function (def) {
                DEF = def;
                return loadExisting();
            })
            .then(function () { renderStep(); })
            .catch(function (e) {
                banner(
                    'The questionnaire API is not available (' + e.message + '). ' +
                    'The form definition is served by agent_autofill/questionnaire_api.py; ' +
                    'mount it with app.include_router(questionnaire_router). ' +
                    'This page deliberately keeps no local copy of the field list, so that ' +
                    'it can never drift from the server-side schema.',
                    'bad'
                );
            });
    }

    window.CairoQuestionnaire = { init: init };
})();
