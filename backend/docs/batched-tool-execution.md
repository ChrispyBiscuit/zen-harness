# Batched tool execution (V1)

Zen already supports multiple native tool calls in one assistant response. BTE retains that protocol. On the full tool path it validates and authorizes the entire group before dispatch, runs certified independent file reads concurrently, and serializes other operations. Read-only certification checks the actual builtin function, not just its advertised name. Every mutation is a global scheduling barrier.

Basic and restricted phases keep their existing tool gates and execute normally, without speculative prefetch. Single calls keep their existing execution path. When BTE is disabled, the previous native dispatch/prefetch behavior is restored.

Use native calls for independent actions. When dependencies or conditions are needed, use `batch_execute`:

```json
{
  "batch": {
    "version": 1,
    "operations": [
      {"id": "write", "tool": "write_file", "args": {"path": "example.py", "content": "print('hello')\n"}},
      {"id": "compile", "tool": "powershell", "args": {"command": "python -m py_compile example.py"}, "depends_on": ["write"], "condition": "all_success"},
      {"id": "test", "tool": "run_tests", "args": {"path": "tests"}, "depends_on": ["compile"], "condition": "all_success"}
    ]
  }
}
```

`always` runs once prerequisites are terminal. `all_success` requires successful prerequisites, including vacuous success with none. `any_failure` requires at least one failed or timed-out prerequisite and cannot have an empty dependency list. Cancellation and skipped nodes do not count as successful prerequisites. Unknown references, cycles, duplicate IDs, malformed arguments, unauthorized paths and denied approvals reject the batch before operations start. This is not a transaction: runtime failures can leave earlier successful operations applied. No automatic write retry or rollback is attempted.

Permissions use the same authorization helper as single calls. A one-use grant is tied to an exact operation call ID, tool and normalized arguments. Batching does not grant blanket authority. Existing tool execution and progress policies still run for each dispatched operation. Nested batches, advisor/delegation calls, worker coordination and background spawning are outside V1. Unsupported tool-schema constraints are rejected rather than silently ignored; use a normal single call for those tools.

## Configuration

`agent.batch_execution` supports:

```json
{
  "enabled": true,
  "max_operations": 8,
  "max_parallel_reads": 4,
  "max_batch_duration_seconds": 120,
  "max_operation_duration_seconds": 60,
  "max_result_tokens": 2000,
  "max_result_tokens_per_operation": 800,
  "preserve_full_outputs": true,
  "retention_batches": 100,
  "retention_bytes": 67108864
}
```

There is no extra model, GPU component or model call in the scheduler. Parallel tools are `read_file`, `list_dir`, `find_files` and `search_text`; shells are always sequential. Limits must be positive, read workers cannot exceed four, and the result budget must fit every status and reference.

Deadlines use cooperative cancellation. Shell commands in a batch cannot silently detach into background work. Running operations must quiesce before another mutation or final batch response. A synchronous tool or third-party client that ignores cancellation can therefore exceed the configured deadline while it drains; BTE does not pretend that Python threads can be safely killed. Operations that complete after their deadline are reported as timed out, with their actual returned output preserved. Hard process isolation for noncooperative third-party tools is not provided in V1.

## Results and context

The aggregate contains batch/operation IDs, terminal states, measured elapsed time, excerpts and retrieval references. Failed/timed-out excerpts retain head and tail. UTF-8 byte budgets provide a conservative token upper bound; this is intentionally not represented as a measured tokenizer count.

Raw results are saved atomically under `<data>/agent_attachments/<conversation>/batches/`. Retention is bounded by count and bytes. `get_batch_result(batch_id, operation_id, offset=0, limit=8000)` returns a raw page, dependency metadata and any image from the operation. It works after compaction and a restart. Retrieval calls bypass automatic result batching, so explicitly requested pages are not repeatedly compacted. Storage failures are reported, and unavailable references are labelled rather than invented.

For native multiple-call responses, the protocol still gets one response per original call ID. The first response carries the logical aggregate; remaining responses are empty acknowledgements. Declarative batches produce one parent response, while child cards and evidence continue through existing Zen infrastructure. The action ledger expands executed child calls at the parent position, preserving write/test ordering.

Batch telemetry records requested operation count, parallel operation count, terminal-state counts, measured execution elapsed milliseconds, raw output bytes and conservative result-token bounds. The normal agent loop already records model token/timing measurements; unavailable model metrics remain unavailable. No speedup is assumed from the existence of batching.

## Evaluation

Lightweight tests exercise validation, permissions, dependency failure propagation, concurrency barriers, cancellation, deadline cleanup, output retention/retrieval, native protocol compatibility and restart activation. Real Bonsai A/B inference trials are deferred while the current FPS task runs.

For an empirical comparison, use the same model/profile, permissions, context, initial files, prompts and independent graders with BTE enabled and disabled. Repeat repository inspection, feature implementation, 3D game development and controlled failure workloads. Include unsuccessful attempts and human interventions. Native multi-call responses already saved model turns before BTE; measure whether the model actually chooses useful dependent batches and whether truncation leads to extra retrieval turns. Primary outcomes are verified task completion and wall-clock time, not token reduction alone.
