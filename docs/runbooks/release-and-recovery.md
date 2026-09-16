# Release, rollback, and recovery runbook

## Release

1. Run `make test`, `make release-gate`, and `make infra-validate`.
2. Review the scorecard fingerprint and segmented results. Update the approved
   baseline only through review; never from the deployment job itself.
3. Trigger the Canary deployment workflow for staging. Verify application
   health, voice error budget, queue depth, migration completion, and one
   synthetic call before promotion.
4. Production promotion requires the protected environment reviewer. Keep the
   canary observable for at least one normal traffic window.

## Rollback

Run `bash scripts/canary_release.sh rollback`. Stop automation templates or AI
capabilities independently with their existing kill switches if the failure is
feature-specific. Database migrations must be expand/contract compatible; do
not deploy a destructive migration in the same release that removes its old
read path.

## Backup and restore

Managed production storage uses point-in-time PostgreSQL backups, object
versioning/lifecycle, and encrypted queue retention. Locally or in staging,
`make recovery-drill` creates a checksummed custom PostgreSQL archive, restores
it into a disposable database whose name is constrained to
`omni_recovery_drill*`, verifies migration metadata, and removes only that drill
database.

Record start/end times, archive checksum, restored schema version, object-store
inventory comparison, outbox replay lag, and whether the 15-minute RPO and
60-minute database RTO were met. Escalate a failed drill as a release blocker.
