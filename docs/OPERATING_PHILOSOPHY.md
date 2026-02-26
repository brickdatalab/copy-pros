# Operating Philosophy

## Purpose

The bot is built to increase wallet value over time across concurrent BTC/ETH/SOL 5m and 15m markets.  
The design target is asymmetric return capture under strict risk limits, not maximal trade count.

## Convexity-First

Convexity-first means cheaper entries can deliver exponentially higher payoff in share terms.  
When prices are distressed, share count matters more than nominal dollar deployment, which is why the reversal layer uses share-forward sizing while still honoring hard wager caps.

## 95-Cent Discipline

The 95-cent discipline is mandatory:

- trigger take-profit when best bid reaches `0.94`
- place exit limit at `0.95`

This avoids expiration-crash and late-manipulation risk.  
The system does not delay exits to chase the final `$0.05`.

## Distressed Manipulation Recognition

Sub-`$0.25` conditions can be either panic pricing or stealth accumulation.  
The reversal layer only activates when divergence supports accumulation:

- strong buy-side imbalance
- rising short VWAP
- flat-to-soft mid price
- momentum turn or acceleration

## Confidence Scaling

Confidence thresholds are tiered:

- normal entries use the default confidence floor (`0.52`)
- reversal entries can use a lower floor (`0.40`) only for bullish distressed setups under `<0.25`

All existing guards still apply (streak persistence, cooldown, exposure limits, price caps).

## What We Optimize

Primary optimization target is convex payoff profile and wallet growth quality:

- expectancy
- profit factor
- realized PnL by reason code

Raw win rate alone is not sufficient; a lower win rate with stronger payoff asymmetry can still outperform.
