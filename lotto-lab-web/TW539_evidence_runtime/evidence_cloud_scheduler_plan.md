# Evidence Runtime Cloud Scheduler Plan

No cloud resource was created in this Gate. The existing Codex heartbeat remains `LOCAL_DEVELOPMENT_ONLY`.

## Options

| Option | 24/7 | Persistent Disk | Concurrency | Isolation | Cost/resources | Observability | Fit |
|---|---|---|---|---|---|---|---|
| Render Cron Job | Strong schedule and single-run guarantee | **Cannot access a Render Persistent Disk** | Managed single run | High | New service; per-run billing and minimum monthly charge | Run history/logs | Not compatible with the required disk-backed Journal unless storage moves to a managed database |
| Existing Render Web Service scheduler | Runs with the existing service | Direct access to its attached disk | Must use the Evidence nonblocking lock | Lower; shares web process | No new resource | Existing logs/metrics | **Recommended after Staging validation**, because it is the only minimal-resource option that can write the existing disk |
| Dedicated background worker | 24/7 | Can attach its own disk, but cannot share the Web Service disk | Independent worker | Strong | New continuously billed service and separate disk/data flow | Worker logs/metrics | Safe isolation, but cannot directly share the existing Production disk |
| External scheduler calling a protected endpoint | 24/7 | Web Service performs the disk write | Endpoint lock required | Medium | Scheduler plus authenticated endpoint | Split logs | More moving parts and expands attack surface |

## Recommendation

Use the **existing Render Web Service's already-controlled scheduler lifecycle**, with one daily call to `python -m tw539_evidence_runtime` logic inside the service and the nonblocking Evidence lock. This requires a separate Staging-only delivery and scheduler Gate and must not run until Prediction provenance inputs are wired and verified. It adds no cloud service and is the only simple option that can access the service's existing Persistent Disk.

Render's official documentation states that Cron Jobs cannot provision or access Persistent Disks, while paid web services and background workers can attach one. Therefore a standalone Render Cron Job is not compatible with this filesystem design unless the Evidence store is first migrated to a managed database.

Rollback: disable only the Evidence schedule/flag; leave the append-only Journal intact. The local Codex heartbeat must be disabled in a later explicitly approved Gate before cloud activation to prevent duplicate attempts.
