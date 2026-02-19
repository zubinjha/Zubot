# Indeed Daily Search Usage

This file is the quick run/stop reference for `indeed_daily_search`.

## Run Manually (Terminal)

From repo root:

```bash
source .venv/bin/activate
python -m src.zubot.daemon.task_cli run indeed_daily_search --payload-json '{"trigger":"manual_real_run"}'
```

If you start from `~`, `cd` first:

```bash
cd /Users/zubinjha/Documents/Projects/Zubot
source .venv/bin/activate
python -m src.zubot.daemon.task_cli run indeed_daily_search --payload-json '{"trigger":"manual_real_run"}'
```

## Stop Manual Run (Recommended)

### Option 1: Ctrl-C in same terminal

- Press `Ctrl-C` once to request graceful cancel.
- Press `Ctrl-C` again to force-stop the CLI process.

The task runner now cancels via a shared cancel event and terminates the subprocess process-group (including task child workers).

### Option 2: Stop from another terminal

```bash
cd /Users/zubinjha/Documents/Projects/Zubot
source .venv/bin/activate
python -m src.zubot.daemon.task_cli stop indeed_daily_search
```

If needed:

```bash
python -m src.zubot.daemon.task_cli stop indeed_daily_search --force
```

## Daemon/Scheduled Runs

For daemon-managed runs, use central run control (UI kill action or central kill endpoint).  
Do not rely on closing random terminals for scheduled runs.

## Optional: Reset DB Before Test

```bash
bash devtools/reset_central_db.sh
```
