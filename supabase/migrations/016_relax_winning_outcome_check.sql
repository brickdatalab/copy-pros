-- Migration 016: Relax winning_outcome CHECK constraint
--
-- The original constraint only allowed 'YES' or 'NO', but Polymarket markets
-- use arbitrary outcome labels: 'Up'/'Down' for eth-updown, 'Higher'/'Lower',
-- candidate names, team names, etc. The CLOB API's tokens[*].outcome field
-- is the authoritative source of the label — we should store it verbatim.
--
-- Also: the resolution_loop() in stream_market.py was changed to use the CLOB
-- REST API (GET /markets/{condition_id}) instead of Gamma's ?conditionIds=
-- query, which returns incorrect data for short-duration markets (hash collision
-- with older markets in Gamma's index).

ALTER TABLE copy_pros.markets
  DROP CONSTRAINT IF EXISTS markets_winning_outcome_check;
