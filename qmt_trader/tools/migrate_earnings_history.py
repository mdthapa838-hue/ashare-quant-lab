"""Preview or explicitly import only top-level legacy earnings CSV snapshots.

Default is dry-run: no database module imported, opened or created. No API calls.
Original CSVs are never rewritten and rows are never deduplicated by stock/date.
"""
from pathlib import Path
from datetime import datetime
import argparse,csv,hashlib,io,json,re,sys

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_HISTORY=ROOT/'earnings_watch/history'

def excluded(path):
    return any(part.startswith('_backup_') or part.startswith('_contaminated_') for part in Path(path).parts)

def discover(history):
    history=Path(history).expanduser().resolve(strict=True)
    if excluded(history) or not history.is_dir():raise ValueError('EXCLUDED_OR_INVALID_HISTORY_DIRECTORY')
    files=[]
    for path in sorted(history.glob('earnings_*.csv')):
        if path.is_symlink() or path.resolve().parent!=history or excluded(path.resolve()):raise ValueError('NON_TOP_LEVEL_OR_LINKED_HISTORY_FILE')
        if not re.fullmatch(r'earnings_\d{8}\.csv',path.name):raise ValueError('AMBIGUOUS_QUERY_DATE_FILENAME:'+path.name)
        if not path.is_file():raise ValueError('HISTORY_ENTRY_NOT_FILE')
        files.append(path)
    return files

def inspect_csv(path):
    raw=path.read_bytes();encoding='utf-8-sig'
    try:content=raw.decode(encoding)
    except UnicodeDecodeError:encoding='gb18030';content=raw.decode(encoding)
    reader=csv.DictReader(io.StringIO(content));rows=list(reader)
    if not reader.fieldnames:raise ValueError('CSV_HEADER_MISSING')
    if any(None in row for row in rows):raise ValueError('CSV_ROW_HAS_EXTRA_COLUMNS')
    query=datetime.strptime(path.stem.removeprefix('earnings_'),'%Y%m%d').date().isoformat()
    return {'path':str(path),'query_date':query,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'row_count':len(rows),'encoding':encoding,'columns':reader.fieldnames,
            'origin':'legacy_csv','complete':False,'original_acquired_at':'UNKNOWN','market_quote_time':'UNKNOWN'}

def migrate(history=DEFAULT_HISTORY,*,execute=False,db_path=None):
    files=discover(history);manifest=[inspect_csv(path) for path in files]
    result={'mode':'EXECUTE' if execute else 'DRY_RUN','source_scope':'TOP_LEVEL_EARNINGS_CSV_ONLY',
      'file_count':len(manifest),'row_count':sum(r['row_count'] for r in manifest),'manifest':manifest,
      'excluded_subdirectories':'_backup_* and _contaminated_* are never searched or imported',
      'source_csv_modified':False,'database_opened':False,'import_results':[],'errors':[],
      'dedup_policy':'Store atomic idempotency only: normalized source path + SHA256 + query_date. Preserve every original row, including multiple report periods per security/date.'}
    if not execute:
        result['status']='READY_FOR_REVIEW_NO_DATABASE_IMPORT';return result
    sys.path.insert(0,str(ROOT))
    from earnings_store import EarningsStore
    store=EarningsStore(db_path=db_path)
    result['database_opened']=True
    try:
        for item in manifest:
            path=Path(item['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                result['errors'].append({'path':str(path),'error':'SOURCE_CHANGED_AFTER_PREVIEW'});continue
            try:
                imported=store.import_csv(path,query_date=item['query_date'])
                result['import_results'].append({'path':str(path),'query_date':item['query_date'],
                  'expected_source_rows':item['row_count'],'source_sha256':item['sha256'],'run':imported})
            except Exception as exc:
                result['errors'].append({'path':str(path),'error_type':type(exc).__name__})
        changed=[x['path'] for x in manifest if hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()!=x['sha256']]
        result['source_files_changed_during_execution']=changed
        result['status']='IMPORT_COMPLETE' if not result['errors'] and not changed else 'PARTIAL_REVIEW_REQUIRED'
    finally:
        close=getattr(store,'close',None)
        if callable(close):close()
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='默认只预览顶层历史CSV；显式--execute才写数据库。')
    parser.add_argument('--history',type=Path,default=DEFAULT_HISTORY)
    parser.add_argument('--execute',action='store_true',help='显式执行已审阅的CSV迁移；默认不打开数据库')
    parser.add_argument('--db-path',type=Path,help='可选数据库路径；不指定则使用EarningsStore默认路径')
    parser.add_argument('--report',type=Path,help='把完整清单/导入回执写入指定JSON（不改原CSV）')
    args=parser.parse_args(argv)
    result=migrate(args.history,execute=args.execute,db_path=args.db_path)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','mode','file_count','row_count','database_opened','source_csv_modified']},ensure_ascii=False))
    return 0 if not result['errors'] else 2

if __name__=='__main__':raise SystemExit(main())
