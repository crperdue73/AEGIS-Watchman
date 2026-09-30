# Demo agent persona file — Layer 1 watches this for changes.
# The demo's run.sh edits a copy of it between two collector passes so an
# identity.drift event is produced, then feeds the timeline so the
# egress-after-identity-change correlation rule can fire.

Name: demo-agent
Role: a stand-in agent whose persona an intruder overwrites
