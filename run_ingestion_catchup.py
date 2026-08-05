"""
Bounded Firebase -> PostgreSQL ingestion catch-up — cron entry point.

ingestion_worker.py's main_loop() is a persistent daemon (poll every
POLL_INTERVAL_SECONDS forever) — the wrong shape for cron, which expects a
short-lived process that exits. This does the same work (run_once() in a
loop, using the same per-station checkpoint) but stops as soon as a cycle
returns no new documents, so cron can just re-run it every few minutes and
each run picks up exactly where the last one left off.

Usage (see crontab -l):
  */5 * * * * <venv python> run_ingestion_catchup.py >> ~/.awh-ingestion-cron.log 2>&1
"""

from ingestion_worker import IngestionWorker, logger


def main():
    worker = IngestionWorker()
    worker.initialize()

    checkpoint = worker.checkpoint.load()
    for _ in range(500):  # safety cap — a single cron run shouldn't loop forever
        new_checkpoint = worker.run_once(checkpoint)
        if new_checkpoint == checkpoint:
            break
        worker.checkpoint.save(new_checkpoint, worker.inserter.total_inserted)
        checkpoint = new_checkpoint
    else:
        logger.warning("Hit the 500-cycle safety cap without fully catching up; will continue next run")

    worker._cleanup()


if __name__ == "__main__":
    main()
