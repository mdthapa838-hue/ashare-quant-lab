# -*- coding: utf-8 -*-
"""Sequential, cancellable collection. Every date has an auditable SQLite run."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from earnings_fetch import FetchWorker, FetchCancelled, AcquisitionError
from earnings_store import EarningsStore

DEFAULT_DATABASE = Path(__file__).resolve().parent / "earnings_watch" / "earnings.sqlite3"

class BatchFetchWorker(QThread):
    progress = Signal(str)
    day_done = Signal(str, object, dict)
    summary = Signal(dict)
    failed = Signal(str)

    def __init__(self, dates, use_tushare=True, store_path=None, skip_completed=True):
        super().__init__()
        self.dates = sorted(set(str(x) for x in dates))
        for value in self.dates:
            if date.fromisoformat(value) > date.today():
                raise ValueError("不能获取未来日期")
        self.use_tushare = bool(use_tushare)
        self.store_path = Path(store_path or DEFAULT_DATABASE)
        self.skip_completed = bool(skip_completed)

    def run(self):
        result = dict(planned=len(self.dates), total=len(self.dates), completed=0,
                      skipped=0, failed=0, partial=0, cancelled=0, rows=0,
                      errors=[], dates={})
        current_run, store = None, None
        try:
            store = EarningsStore(self.store_path)
            completed = (store.completed_dates(self.dates[0], self.dates[-1], self.use_tushare)
                         if self.dates and self.skip_completed else set())
            for index, day in enumerate(self.dates, 1):
                if self.isInterruptionRequested():
                    result["cancelled"] = 1
                    break
                if day in completed:
                    result["skipped"] += 1
                    result["dates"][day] = "skipped"
                    self.progress.emit(f"{index}/{len(self.dates)} {day} 已完整保存，跳过")
                    continue
                current_run = store.begin_run(day, self.use_tushare, origin="network")
                worker = FetchWorker(day, self.use_tushare, cancel_check=self.isInterruptionRequested)
                worker.progress.connect(self.progress.emit)
                try:
                    self.progress.emit(f"日期 {index}/{len(self.dates)}：{day}")
                    frame = worker._fetch()
                    worker._check()
                    meta = dict(worker.metadata)
                    quote_times = sorted({str(v) for v in frame.get("行情时间", [])
                                          if v is not None and str(v) not in ("", "未知", "nan", "None")})
                    meta["quote_times"] = quote_times
                    meta["quote_time"] = (quote_times[0] if len(quote_times) == 1 else
                                          " ~ ".join((quote_times[0], quote_times[-1])) if quote_times else None)
                    meta["fetched_at"] = meta.get("fetch_finished_at") or __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
                    status = "success" if meta.get("complete") is True else "partial"
                    rows = frame.to_dict(orient="records")
                    store.finish_run(current_run, rows, meta, status=status)
                    result["completed" if status == "success" else "partial"] += 1
                    result["rows"] += len(rows)
                    result["dates"][day] = status
                    meta.update(run_id=current_run, status=status)
                    # Snapshot is committed before the UI is told it is available.
                    self.day_done.emit(day, frame, meta)
                    current_run = None
                except FetchCancelled:
                    store.fail_run(current_run, "用户取消，本日期未完成", status="cancelled")
                    current_run = None
                    result["cancelled"] = 1
                    result["dates"][day] = "cancelled"
                    break
                except Exception as exc:
                    # Never persist provider messages containing a possible token or URL.
                    message = str(exc) if isinstance(exc, AcquisitionError) else type(exc).__name__
                    store.fail_run(current_run, message, status="failed")
                    current_run = None
                    result["failed"] += 1
                    result["errors"].append({"date": day, "error": message})
                    result["dates"][day] = "failed"
                    self.progress.emit(f"{day} 失败：{message}；继续后续日期")
            result["pending"] = len(self.dates) - len(result["dates"])
        except Exception as exc:
            message = type(exc).__name__
            if store is not None and current_run is not None:
                try:
                    store.fail_run(current_run, message, status="failed")
                except Exception:
                    pass
            result["fatal_error"] = message
            self.failed.emit(f"批量任务失败（{message}），请检查数据库是否可写")
        finally:
            self.summary.emit(result)
