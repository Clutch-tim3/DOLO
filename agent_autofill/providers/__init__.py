"""
Cloud-storage providers for Agent Autofill (Google Drive, Dropbox).

=============================================================================
UNVERIFIED
=============================================================================
No provider in this package has ever completed an OAuth consent flow, called a
live API, or received a real webhook. That requires entering account
credentials, which was prohibited in the environment this was built in, and
there is no test account and no registered OAuth client.

Verified offline, with literal test output:

    * Google and Dropbox webhook verification against crafted valid and
      invalid requests -- missing signature, wrong signature, unknown channel,
      replayed/stale delivery, expired channel.
    * Token encryption round-trip through Fernet.
    * Channel-renewal arithmetic, including a proof that a daily cron cannot
      keep a 24-hour channel alive.
    * That no token reaches a log statement or a browser-servable path.

Not verified, and not claimed to be:

    * the consent screens and the scopes as the user actually sees them,
    * `files.watch` registration,
    * any real notification arriving at the receiver,
    * `/files/list_folder/continue` returning real entries,
    * renewal observed across a real 24-hour channel lifetime.

`VERIFICATION.md` in this directory is the checklist for closing that gap. Do
not describe this package as working until it has been run.

=============================================================================
LAYOUT
=============================================================================
    base_provider.py        abstract interface + shared value types
    provider_db.py          where credentials live, and the guard that keeps
                            them out of static/ and firebase_public/
    token_store.py          Fernet encryption at rest, log scrubbing
    channel_registry.py     the channel/cursor state webhook verification
                            checks against
    google_drive_provider.py
    dropbox_provider.py

Nothing here is imported by `app.py` or `main.py`. Wiring the receivers in is
a separate step; see `agent_autofill/webhooks/routes.py`.
"""

from agent_autofill.providers.base_provider import (  # noqa: F401
    BaseCloudProvider,
    ChangedFile,
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderSDKMissing,
    WebhookChannel,
    WebhookVerdict,
)

__all__ = [
    "BaseCloudProvider",
    "ChangedFile",
    "ProviderError",
    "ProviderSDKMissing",
    "ProviderConfigError",
    "ProviderAuthError",
    "WebhookChannel",
    "WebhookVerdict",
]
