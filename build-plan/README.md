# Build Plan Index

Start here before implementation.

## Documents
- `MASTER_PLAN.md` - full architecture + constraints + runtime approach
- `FILEMAP.md` - target code structure
- `ENV_SPEC.md` - env variables and defaults
- `LOOP_CONTRACTS.md` - async loop boundaries and non-overlap rules
- `SUPABASE_TRACKING_SCHEMA.sql` - tracking tables for decisions/orders/outcomes
- `IMPLEMENTATION_CHECKLIST.md` - phase-by-phase execution checklist
- `TUI_GUIDE.md` - terminal operator UX spec

## Immediate Next Step
Implement directly against this build-plan package, starting with:
1. package scaffold
2. event metadata + remaining-time resolver
3. ws ingest and state engine

