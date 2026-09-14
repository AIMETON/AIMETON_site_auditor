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

Implementation SHA `153126855af10e8a45f2bf2bd193316e6c25892a` passed the full
local suite (1386 passed, 1 expected xfail, 6 subtests), Baseline CI run
34798185817 and Acceptance Governance run 34798200106. No stage acceptance is
claimed before an authorized merge and exact-SHA deployment.

## Stage checkpoint and responsiveness correction — 2026-09-14T02:54:18Z

PR #907 is merged and deployed at exact SHA
`3377b758fd48763a83bbcd9a407c417353434f3c`; post-merge Baseline CI
34800345992 and Deploy Stage 34800405291 succeeded. Live evidence confirms
continued execution at 74.3 seconds after all 20 search directions, so the
no-aggregate-deadline invariant is GREEN.

Stop responsiveness under candidate load is RED: two requests exceeded 30 and
120 seconds, although a reduced run returned `stopped` in 7.79 seconds. The
corrective PR #908 candidate offloads synchronous DNS validation, HTML parsing
and heuristic analysis from the asyncio event loop. This is a responsiveness
change, not a new search budget: work is still awaited to completion unless the
user explicitly stops it. Stage acceptance must demonstrate progress polling and
explicit stop while a large candidate pool is actively being inspected.
