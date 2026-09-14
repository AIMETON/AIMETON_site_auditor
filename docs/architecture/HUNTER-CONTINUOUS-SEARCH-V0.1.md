# Hunter continuous search v0.1

Checkpoint: 2026-09-14T02:03:12Z. Superseding implementation candidate in PR
#907; not merged or deployed.

Owner decision: Hunter search must not stop or reduce candidate inspection merely
because a wall-clock threshold has elapsed. The interface communicates continued
work and lets the user explicitly stop when the partial result is sufficient.

## Contract

- The browser starts Hunter asynchronously and receives an opaque run id plus a
  separate high-entropy run token. Status and stop calls require that token.
- No aggregate wall-clock deadline is applied to search execution or candidate
  inspection. Provider request safety limits, concurrency, administrator policy,
  evidence gates and explicit result-policy limits remain unchanged.
- Progress reports elapsed time, search directions completed/total and candidate
  inspections completed/total. The status text states that search continues and
  offers the choice to wait or stop.
- Stop requires a deliberate user action and confirmation. It cancels the active
  run and returns only candidate checks completed before cancellation. Pending
  work is not converted into synthetic shallow conclusions.
- Existing `POST /api/hunt` remains synchronous for compatibility. The browser
  uses `POST /api/hunt/start`, authenticated-by-token status polling, and an
  explicit stop endpoint.
- The v0.1 controller and task handles are process-local. A runtime restart can
  interrupt the job and lose its status projection; durable recovery and
  cross-instance cancellation are follow-up requirements, not claimed behavior.

## Acceptance

Regression tests must prove that a candidate check exceeding the rejected short
deadline completes normally, progress includes completed work, explicit stop
returns completed candidates, an invalid run token cannot inspect a run, and the
legacy regime response contract remains intact. Deployment acceptance requires
an exact-SHA long-running Hunter smoke where progress remains observable beyond
the former failure window and either completes fully or stops only by user action.
