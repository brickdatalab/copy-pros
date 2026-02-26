# Terminal UI Guide (Inspired by video frames)

## Design Intent
Clean operator-first dashboard with low visual noise and high action clarity.

## Layout
- Header row:
  - bot name
  - mode (`LIVE` / `DRY-RUN`)
  - event slug
  - remaining time
  - loop health badge
- Body panel 1 (left):
  - active indicators (compact values + arrows)
- Body panel 2 (center):
  - decision resolver output
  - reason codes + confidence
- Body panel 3 (right):
  - risk budget bars (per-side USDC + open exposure)
- Bottom tape:
  - latest orders/fills/cancels/rejects

## Update Frequency
- header + health: 4 Hz
- indicators + signals: 5-10 Hz
- order tape: event-driven

## Colors
- green: fills, profitable exits, healthy
- yellow: pending, replace, reconnect
- red: reject, error, risk block
- cyan: informational state transitions

## Must-Haves
- never clear terminal faster than user can scan
- keep latest 10-20 order tape rows visible
- always show current side exposure and remaining side budget

