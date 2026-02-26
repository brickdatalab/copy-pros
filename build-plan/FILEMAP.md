# Target File Map (New Runtime)

## Repo Direction
Current code is expendable; this file map defines the production path.

```text
copy-pros/
  build-plan/
    MASTER_PLAN.md
    FILEMAP.md
    ENV_SPEC.md
    SUPABASE_TRACKING_SCHEMA.sql
    IMPLEMENTATION_CHECKLIST.md
    LOOP_CONTRACTS.md

  trader/
    __init__.py
    cli.py
    config.py
    main.py

    domain/
      event_context.py
      models.py
      enums.py

    adapters/
      polymarket/
        auth.py
        rest_client.py
        ws_client.py
        dto.py
      supabase/
        writer.py
        models.py

    engine/
      state.py
      orderbook.py
      windows.py
      indicators/
        base.py
        registry.py
        vwap.py
        imbalance.py
        spread_momentum.py
        mid_momentum.py
        microprice.py
      signals/
        base.py
        registry.py
        momentum_alignment.py
        spread_reversion.py
        pressure_breakout.py
        resolver.py

    risk/
      constraints.py
      sizing.py
      exposure.py

    execution/
      router.py
      lifecycle.py
      intents.py
      order_state.py

    runtime/
      orchestrator.py
      loops.py
      queues.py
      shutdown.py

    tracking/
      event_buffer.py
      supabase_sink.py
      run_summary.py
      accuracy.py

    ui/
      console.py
      panels.py
      formatter.py

  scripts/
    run_event_bot.py

  supabase/
    migrations/
      017_bot_tracking_tables.sql

  tests/
    ... (kept minimal but real)
```

## Why this structure
- Clear boundaries for plug-and-play indicators and signal modules.
- Per-module contracts reduce overhaul risk when formulas change.
- Async runtime modules isolate hot path from tracking overhead.

