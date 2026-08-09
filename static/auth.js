/* ==========================================================================
   CairoAI — the sign-in gate.
   --------------------------------------------------------------------------
   Every page that calls a company-aware endpoint loads this.

   WHAT IT REPLACES
   Until now the client decided which company it was:

       function getCompanyId() {
           return localStorage.getItem('cairoai-company-id') || 'pro_corp';
       }

   ...and sent that in an X-Company-ID header. The server believed it. Editing
   one localStorage key in the console was enough to become any tenant, and
   sending the header from curl was enough to be one without a browser at all.

   The identity now comes from the server. /api/auth/me answers 401 until a
   session exists; this puts up a login form until it stops doing that. The
   session lives in an HttpOnly cookie, which is the point: no script on the
   page — ours or an injected one — can read it, so there is nothing left for a
   getCompanyId() to return.

   RENDERING
   Built from DOM nodes with textContent, never innerHTML, matching the rest of
   the app. Styles are inline here rather than in style.css so that adding the
   gate does not require bumping the stylesheet cache-buster (CLAUDE.md trap 5)
   and cannot lose a specificity fight with the #tab-agent rules (trap 4).
   ========================================================================== */
(function (global) {
    'use strict';

    var identity = null;
    var overlay = null;
    var resolveReady;
    var ready = new Promise(function (resolve) { resolveReady = resolve; });

    function el(tag, styles, text) {
        var n = document.createElement(tag);
        if (styles) { n.setAttribute('style', styles); }
        if (text !== undefined && text !== null) { n.textContent = String(text); }
        return n;
    }

    /* ------------------------------------------------------------------ api */

    async function whoami() {
        try {
            // same-origin credentials are the default, but stating it means a
            // future change to a different origin fails loudly rather than
            // silently dropping the cookie.
            var res = await fetch('/api/auth/me', { credentials: 'same-origin' });
            if (!res.ok) { return null; }
            return await res.json();
        } catch (e) {
            return null;
        }
    }

    async function login(username, password) {
        var res = await fetch('/api/auth/login', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            // Body, not query string: the runtime logs the request line before
            // any handler runs, so a password in a URL is a password in the
            // access log no matter what we do afterwards.
            body: JSON.stringify({ username: username, password: password })
        });
        if (!res.ok) {
            var detail = 'Incorrect username or password.';
            try { detail = (await res.json()).detail || detail; } catch (e) { /* no body */ }
            throw new Error(detail);
        }
        return await res.json();
    }

    async function logout() {
        try {
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
        } catch (e) { /* signing out is best-effort from the client's side */ }
        // A full reload rather than re-rendering: every page here reads the
        // identity during its own init, and half-clearing that in place is how
        // stale company data ends up on screen after a switch of account.
        global.location.reload();
    }

    /* --------------------------------------------------------------- the gate */

    function buildOverlay() {
        var wrap = el('div',
            'position:fixed;inset:0;z-index:99999;background:#0b0b0d;' +
            'display:flex;align-items:center;justify-content:center;padding:24px;' +
            'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;');

        var card = el('form',
            'width:100%;max-width:360px;background:#141417;border:1px solid #2a2a30;' +
            'border-radius:14px;padding:28px 24px;box-shadow:0 20px 60px rgba(0,0,0,.6);');

        card.appendChild(el('div',
            'font-size:20px;font-weight:600;color:#f5f5f7;margin-bottom:6px;', 'CairoAI'));
        card.appendChild(el('div',
            'font-size:13px;color:#8b8b95;margin-bottom:22px;line-height:1.5;',
            'Sign in to continue.'));

        function field(labelText, type, autocomplete) {
            var label = el('label',
                'display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;' +
                'color:#8b8b95;margin-bottom:6px;', labelText);
            var input = el('input',
                'width:100%;box-sizing:border-box;background:#0b0b0d;border:1px solid #2a2a30;' +
                'border-radius:8px;padding:10px 12px;color:#f5f5f7;font-size:14px;' +
                'margin-bottom:16px;outline:none;');
            input.type = type;
            input.autocomplete = autocomplete;
            input.required = true;
            card.appendChild(label);
            card.appendChild(input);
            return input;
        }

        var usernameInput = field('Username', 'text', 'username');
        var passwordInput = field('Password', 'password', 'current-password');

        var error = el('div',
            'font-size:12px;color:#e57373;min-height:16px;margin-bottom:12px;line-height:1.4;');
        card.appendChild(error);

        var button = el('button',
            'width:100%;background:#c8a24a;border:none;border-radius:8px;padding:11px;' +
            'color:#0b0b0d;font-size:14px;font-weight:600;cursor:pointer;', 'Sign in');
        button.type = 'submit';
        card.appendChild(button);

        card.appendChild(el('div',
            'font-size:11px;color:#5f5f68;margin-top:18px;line-height:1.5;',
            'Accounts are created by an operator. There is no self-service ' +
            'sign-up and no password reset by email.'));

        card.addEventListener('submit', async function (e) {
            e.preventDefault();
            error.textContent = '';
            button.disabled = true;
            button.textContent = 'Signing in…';
            try {
                await login(usernameInput.value, passwordInput.value);
                // Reload rather than tearing the overlay down: pages fetch their
                // company data during init, and that init already ran and failed.
                global.location.reload();
            } catch (err) {
                error.textContent = err.message;
                button.disabled = false;
                button.textContent = 'Sign in';
                passwordInput.value = '';
                passwordInput.focus();
            }
        });

        wrap.appendChild(card);
        return { wrap: wrap, username: usernameInput };
    }

    function showGate() {
        if (overlay) { return; }
        var built = buildOverlay();
        overlay = built.wrap;
        document.body.appendChild(overlay);
        built.username.focus();
    }

    /* Any later 401 means the session went away mid-visit — an expiry, a
       revocation, or an operator disabling the account. Put the gate back
       rather than letting the page sit there showing data it can no longer
       refresh. */
    function handleUnauthorized() {
        identity = null;
        showGate();
    }

    async function init() {
        identity = await whoami();
        if (!identity) {
            showGate();
        } else {
            document.dispatchEvent(new CustomEvent('cairoai:auth', { detail: identity }));
        }
        resolveReady(identity);
        return identity;
    }

    global.CairoAuth = {
        ready: ready,
        init: init,
        whoami: whoami,
        logout: logout,
        handleUnauthorized: handleUnauthorized,
        identity: function () { return identity; },
        // The authenticated company, for the few places that still want to
        // label something with it. It is display only — the server never reads
        // a company from the client any more.
        companyId: function () { return identity ? identity.company_id : null; },
        tier: function () { return identity ? identity.tier : null; }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})(window);
