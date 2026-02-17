# trace_ping

Purpose:
- Minimal predefined task for queue/agent wiring verification.
- Every execution appends one JSON row to `state/run_trace.jsonl`.

Entrypoint:
- `src/zubot/predefined_tasks/trace_ping/task.py`

How to verify:
1. Queue the task (`task_id=trace_ping`) from UI or tool call.
2. Confirm run status in daemon Task Slots / Runs.
3. Inspect `src/zubot/predefined_tasks/trace_ping/state/run_trace.jsonl`.
