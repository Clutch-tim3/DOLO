#!/usr/bin/env bash
#
# A12 — create the error alert policy and the email channel it notifies.
#
# NOT RUN AUTOMATICALLY. This creates resources in your GCP project and sends
# mail to an address you choose, so it waits for you. Nothing here is
# destructive: both commands create, neither deletes or overwrites.
#
#   ./ops/apply_error_alerting.sh you@example.com          # create
#   ./ops/apply_error_alerting.sh you@example.com --dry-run
#
# On this machine gcloud must be called as gcloud.cmd from Git Bash — the .ps1
# wrapper is blocked (LAUNCH_PLAN section 6). GCLOUD below handles both.

set -euo pipefail

PROJECT="${PROJECT:-cairoai}"
EMAIL="${1:-}"
DRY_RUN="${2:-}"
POLICY_FILE="$(dirname "$0")/error_rate_alert_policy.json"

if [ -z "$EMAIL" ]; then
  echo "usage: $0 <email-address> [--dry-run]" >&2
  exit 2
fi

if command -v gcloud >/dev/null 2>&1; then
  GCLOUD="gcloud"
else
  GCLOUD="/c/Users/$USER/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
fi

run() {
  if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "would run: $*"
  else
    "$@"
  fi
}

echo "project : $PROJECT"
echo "email   : $EMAIL"
echo "policy  : $POLICY_FILE"
echo "mode    : ${DRY_RUN:-apply}"
echo

# 1. The notification channel. Google emails a confirmation link that must be
#    clicked before the channel delivers anything — an unverified channel
#    silently drops alerts, which is the failure mode this whole item exists to
#    remove, so check your inbox after running this.
echo "--- notification channel ---"
run "$GCLOUD" alpha monitoring channels create \
  --project="$PROJECT" \
  --display-name="CairoAI alerts" \
  --type=email \
  --channel-labels="email_address=$EMAIL"

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo
  echo "Dry run. Re-run without --dry-run to create these."
  exit 0
fi

CHANNEL=$("$GCLOUD" alpha monitoring channels list \
  --project="$PROJECT" \
  --filter="displayName='CairoAI alerts'" \
  --format="value(name)" | head -1)

if [ -z "$CHANNEL" ]; then
  echo "Could not find the channel just created. Stopping rather than creating a policy that notifies nobody." >&2
  exit 1
fi
echo "channel: $CHANNEL"

# 2. The policy itself, wired to that channel.
echo
echo "--- alert policy ---"
"$GCLOUD" alpha monitoring policies create \
  --project="$PROJECT" \
  --policy-from-file="$POLICY_FILE" \
  --notification-channels="$CHANNEL"

echo
echo "Done. Verify it actually fires before trusting it:"
echo
echo "  1. Confirm the channel — click the link Google emailed to $EMAIL."
echo "  2. Trigger a real error and wait for the mail. An alert policy nobody"
echo "     has seen fire is not monitoring, it is a configuration."
