"""Append-only SQLite earnings snapshots. Stdlib only; no GUI/network/secrets.

Each method owns its SQLite connection, so one store can serve GUI and worker
threads. Completed runs are immutable. Row JSON preserves every source column.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import csv,hashlib,io,json,math,numbers,os,re,sqlite3

SCHEMA_VERSION=1
TERMINAL_STATUSES={'success','partial','failed','cancelled'}
TZ=timezone(timedelta(hours=8))

def default_db_path(project_dir=None):
    return Path(project_dir or Path(__file__).resolve().parent)/'earnings_watch'/'earnings.sqlite3'

def safe_path(path=None,project_dir=None):
    """Resolve an explicitly selected DB file; never treat a directory as DB."""
    result=Path(path).expanduser() if path is not None else default_db_path(project_dir)
    if result.exists() and result.is_dir():raise ValueError('DATABASE_PATH_IS_DIRECTORY')
    return result.resolve(strict=False)

def _date(value):
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    text=str(value).strip()
    if re.fullmatch(r'\d{8}',text):return datetime.strptime(text,'%Y%m%d').date().isoformat()
    return date.fromisoformat(text).isoformat()

def _now():return datetime.now(TZ).isoformat(timespec='microseconds')

def _normalize(value):
    if value is None or isinstance(value,(str,bool)):return value
    if isinstance(value,numbers.Integral):return int(value)
    if isinstance(value,(numbers.Real,Decimal)):
        number=float(value);return number if math.isfinite(number) else None
    if type(value).__name__ in ('NAType','NaTType'):return None
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,dict):
        if any(not isinstance(k,str) for k in value):raise TypeError('ROW_OR_METADATA_KEYS_MUST_BE_STRINGS')
        return {k:_normalize(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_normalize(v) for v in value]
    if hasattr(value,'item'):
        item=value.item()
        if type(item) is type(value):raise TypeError('UNSUPPORTED_SCALAR_TYPE:'+type(value).__name__)
        return _normalize(item)
    raise TypeError('UNSUPPORTED_JSON_TYPE:'+type(value).__name__)

def _json(value):return json.dumps(_normalize(value),ensure_ascii=False,allow_nan=False,separators=(',',':'))
def _flag(value,nullable=False):
    if value is None and nullable:return None
    value=_normalize(value)
    if not isinstance(value,bool):raise TypeError('USE_TUSHARE_MUST_BE_BOOLEAN')
    return int(value)
def _text(value):return '' if value is None else str(value).strip()

def _identity(row):
    code=_text(row.get('代码',row.get('ts_code',row.get('code'))))
    title=_text(row.get('标题',row.get('title')))
    url=_text(row.get('链接',row.get('URL',row.get('url'))))
    identity=json.dumps([url,code,title],ensure_ascii=False,separators=(',',':'))
    if not any((url,code,title)):identity='EMPTY_IDENTITY_PAYLOAD:'+json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(identity.encode('utf-8')).hexdigest(),code,title,url

class EarningsStore:
    def __init__(self,db_path=None):
        self.db_path=safe_path(db_path);self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self._connection() as c:
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,SCHEMA_VERSION):raise ValueError('UNSUPPORTED_DATABASE_SCHEMA_VERSION')
            existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if existing and not existing.issubset({'runs','announcements','row_snapshots'}):raise ValueError('DATABASE_CONTAINS_UNRELATED_TABLES')
            c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=NORMAL')
            c.execute('BEGIN IMMEDIATE')
            try:
                c.execute('''CREATE TABLE IF NOT EXISTS runs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,query_date TEXT NOT NULL,
                  use_tushare INTEGER CHECK(use_tushare IN(0,1) OR use_tushare IS NULL),origin TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN('running','success','partial','failed','cancelled')),
                  started_at TEXT NOT NULL,finished_at TEXT,fetched_at TEXT,market_time TEXT,source TEXT,
                  completeness TEXT NOT NULL DEFAULT '未完成',complete INTEGER NOT NULL DEFAULT 0,
                  row_count INTEGER NOT NULL DEFAULT 0,error TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',import_key TEXT UNIQUE)''')
                c.execute('''CREATE TABLE IF NOT EXISTS announcements(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,identity_key TEXT NOT NULL UNIQUE,
                  code TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,created_at TEXT NOT NULL)''')
                c.execute('''CREATE TABLE IF NOT EXISTS row_snapshots(
                  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                  ordinal INTEGER NOT NULL,announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE RESTRICT,
                  row_json TEXT NOT NULL,PRIMARY KEY(run_id,ordinal))''')
                c.execute('CREATE INDEX IF NOT EXISTS idx_runs_date_status ON runs(query_date,status,finished_at,id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_snapshot_announcement ON row_snapshots(announcement_id)')
                c.execute('PRAGMA user_version=1');c.commit()
            except BaseException:c.rollback();raise

    @contextmanager
    def _connection(self):
        c=sqlite3.connect(str(self.db_path),timeout=30,isolation_level=None)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA foreign_keys=ON')
        try:yield c
        finally:c.close()

    @staticmethod
    def _run(row):
        if row is None:return None
        result=dict(row);result['run_id']=result['id'];result['use_tushare']=None if result['use_tushare'] is None else bool(result['use_tushare'])
        result['complete']=bool(result['complete']);result['metadata']=json.loads(result.pop('metadata_json'));result.pop('import_key',None)
        return result

    def _insert_run(self,c,day,use_tushare,origin,import_key=None):
        cursor=c.execute("INSERT INTO runs(query_date,use_tushare,origin,status,started_at,import_key) VALUES(?,?,?,'running',?,?)",
                         (day,use_tushare,origin,_now(),import_key))
        return cursor.lastrowid

    def begin_run(self,query_date,use_tushare,origin='network'):
        day=_date(query_date);origin=str(origin).strip()
        if not origin:raise ValueError('ORIGIN_REQUIRED')
        option=_flag(use_tushare,nullable=origin!='network')
        with self._connection() as c:return self._insert_run(c,day,option,origin)

    def _finish(self,c,run_id,rows,metadata,status):
        current=c.execute('SELECT * FROM runs WHERE id=?',(int(run_id),)).fetchone()
        if current is None:raise KeyError('RUN_NOT_FOUND')
        if current['status']!='running':raise ValueError('RUN_ALREADY_FINISHED_IMMUTABLE')
        now=_now();identities=[]
        for ordinal,row in enumerate(rows):
            identity,code,title,url=_identity(row);identities.append(identity)
            c.execute('INSERT OR IGNORE INTO announcements(identity_key,code,title,url,created_at) VALUES(?,?,?,?,?)',(identity,code,title,url,now))
            aid=c.execute('SELECT id FROM announcements WHERE identity_key=?',(identity,)).fetchone()[0]
            c.execute('INSERT INTO row_snapshots(run_id,ordinal,announcement_id,row_json) VALUES(?,?,?,?)',(run_id,ordinal,aid,_json(row)))
        meta=dict(metadata);meta.setdefault('duplicate_identity_rows',len(identities)-len(set(identities)))
        complete=current['origin']=='network' and status=='success' and meta.get('complete') is True
        legacy=current['origin']=='legacy_csv'
        completeness='完整' if complete else '历史CSV导入（完整性未知）' if legacy else '部分' if status=='partial' else '失败' if status=='failed' else '已取消' if status=='cancelled' else '完整性未知'
        fetched=meta.get('fetched_at',None if legacy else now)
        market=meta.get('quote_time',meta.get('market_time',meta.get('行情时间')))
        source=meta.get('source',meta.get('数据来源',current['origin']))
        if not isinstance(source,(str,type(None))):source=_json(source)
        error=meta.get('error')
        if error is not None and not isinstance(error,str):error=_json(error)
        c.execute('''UPDATE runs SET status=?,finished_at=?,fetched_at=?,market_time=?,source=?,completeness=?,
                     complete=?,row_count=?,error=?,metadata_json=? WHERE id=?''',
                  (status,now,_text(fetched) or None,_text(market) or None,source,completeness,int(complete),len(rows),error,_json(meta),run_id))
        return self._run(c.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone())

    def finish_run(self,run_id,rows,metadata,status='success'):
        if status not in TERMINAL_STATUSES:raise ValueError('INVALID_TERMINAL_RUN_STATUS')
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise TypeError('ROWS_MUST_BE_LIST_OF_DICTIONARIES')
        if not isinstance(metadata,dict):raise TypeError('METADATA_MUST_BE_DICTIONARY')
        rows=_normalize(rows);metadata=_normalize(metadata)
        with self._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            try:result=self._finish(c,int(run_id),rows,metadata,status);c.commit();return result
            except BaseException:c.rollback();raise

    def fail_run(self,run_id,error,status='failed'):
        if status not in ('failed','cancelled'):raise ValueError('FAIL_RUN_STATUS_MUST_BE_FAILED_OR_CANCELLED')
        return self.finish_run(run_id,[],{'complete':False,'error':str(error)},status=status)

    def latest_run(self,query_date,use_tushare=None,success_only=True):
        sql='SELECT * FROM runs WHERE query_date=?';args=[_date(query_date)]
        if use_tushare is not None:sql+=' AND use_tushare=?';args.append(_flag(use_tushare))
        if success_only:sql+=" AND status='success'"
        sql+=' ORDER BY COALESCE(finished_at,started_at) DESC,id DESC LIMIT 1'
        with self._connection() as c:return self._run(c.execute(sql,args).fetchone())

    @staticmethod
    def _range(start,end):
        start,end=_date(start),_date(end)
        if end<start:raise ValueError('DATE_RANGE_REVERSED')
        return start,end

    @staticmethod
    def _decorate(payload,run):
        row=dict(payload);legacy=run['origin']=='legacy_csv';meta=run['metadata']
        def known(value):
            text=_text(value)
            return bool(text and text.lower() not in ('none','null','nan','nat','—','-') and not text.lower().startswith(('未知','unknown')))
        row_time=payload.get('行情时间')
        market_time=None if legacy else row_time if known(row_time) else run['market_time']
        row_basis=payload.get('行情口径',payload.get('行情基准'))
        quote_basis='legacy CSV snapshot; original quote time unknown' if legacy else row_basis if known(row_basis) else meta.get('quote_basis','未记录')
        added={'查询日期':run['query_date'],'获取时间':run['fetched_at'] or ('原获取时间未知；CSV导入于 '+run['finished_at'] if legacy else '未知'),
            '行情时间':market_time or ('未知（旧CSV无行情时间）' if legacy else '未知'),
            '行情基准':quote_basis,
            '数据来源':run['source'] or run['origin'],'完整性':run['completeness'],'运行ID':run['id']}
        for name,value in added.items():
            key=name
            while key in row:key='存档_'+key
            row[key]=value
        return row

    def query_rows(self,start_date,end_date,latest_only=True,use_tushare=None,*,include_partial=False):
        start,end=self._range(start_date,end_date);statuses="('success','partial')" if include_partial else "('success')"
        sql='SELECT * FROM runs WHERE query_date BETWEEN ? AND ? AND status IN '+statuses;args=[start,end]
        if use_tushare is not None:sql+=' AND use_tushare=?';args.append(_flag(use_tushare))
        sql+=" ORDER BY query_date ASC,"+("CASE WHEN origin='network' THEN 0 ELSE 1 END ASC," if latest_only else '')+'finished_at DESC,id DESC'
        with self._connection() as c:
            runs=[];seen=set()
            for raw in c.execute(sql,args):
                run=self._run(raw)
                if latest_only and run['query_date'] in seen:continue
                seen.add(run['query_date']);runs.append(run)
            snapshots={r['id']:[] for r in runs};ids=list(snapshots)
            for offset in range(0,len(ids),400):
                chunk=ids[offset:offset+400];marks=','.join('?' for _ in chunk)
                for row in c.execute('SELECT run_id,ordinal,row_json FROM row_snapshots WHERE run_id IN ('+marks+') ORDER BY run_id,ordinal',chunk):
                    snapshots[row['run_id']].append(json.loads(row['row_json']))
            return [self._decorate(row,run) for run in runs for row in snapshots[run['id']]]

    def list_runs(self,start_date=None,end_date=None):
        sql='SELECT * FROM runs WHERE 1=1';args=[]
        if start_date is not None:sql+=' AND query_date>=?';args.append(_date(start_date))
        if end_date is not None:sql+=' AND query_date<=?';args.append(_date(end_date))
        if start_date is not None and end_date is not None:self._range(start_date,end_date)
        sql+=' ORDER BY query_date DESC,COALESCE(finished_at,started_at) DESC,id DESC'
        with self._connection() as c:return [self._run(r) for r in c.execute(sql,args)]

    def completed_dates(self,start,end,use_tushare):
        start,end=self._range(start,end);option=_flag(use_tushare)
        with self._connection() as c:
            return {r[0] for r in c.execute("SELECT DISTINCT query_date FROM runs WHERE query_date BETWEEN ? AND ? AND use_tushare=? AND origin='network' AND status='success' AND complete=1",(start,end,option))}

    def stats(self):
        with self._connection() as c:
            statuses={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM runs GROUP BY status')}
            limits=c.execute('SELECT MIN(query_date),MAX(query_date),COUNT(DISTINCT query_date) FROM runs').fetchone()
            return {'database_path':str(self.db_path),'schema_version':c.execute('PRAGMA user_version').fetchone()[0],
                'journal_mode':c.execute('PRAGMA journal_mode').fetchone()[0],'runs':sum(statuses.values()),'status_counts':statuses,
                'snapshots':c.execute('SELECT COUNT(*) FROM row_snapshots').fetchone()[0],
                'announcements':c.execute('SELECT COUNT(*) FROM announcements').fetchone()[0],
                'complete_network_runs':c.execute("SELECT COUNT(*) FROM runs WHERE origin='network' AND status='success' AND complete=1").fetchone()[0],
                'query_dates':limits[2],'start_date':limits[0],'end_date':limits[1]}

    def import_csv(self,path,query_date=None):
        source=Path(path).expanduser().resolve(strict=True)
        if not source.is_file():raise ValueError('CSV_PATH_NOT_FILE')
        raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest();encoding='utf-8-sig'
        try:text=raw.decode(encoding)
        except UnicodeDecodeError:encoding='gb18030';text=raw.decode(encoding)
        if query_date is None:
            found=set()
            for token in re.findall(r'(?<!\d)(\d{4}-\d{2}-\d{2}|\d{8})(?!\d)',source.name):
                try:found.add(_date(token))
                except ValueError:pass
            if len(found)!=1:raise ValueError('CSV_QUERY_DATE_REQUIRED_OR_AMBIGUOUS')
            day=next(iter(found))
        else:day=_date(query_date)
        reader=csv.DictReader(io.StringIO(text,newline=''));fields=reader.fieldnames
        if not fields or any(f is None or not str(f).strip() for f in fields) or len(set(fields))!=len(fields):raise ValueError('CSV_HEADERS_MISSING_OR_DUPLICATED')
        rows=[]
        for row in reader:
            if None in row:raise ValueError('CSV_ROW_HAS_MORE_VALUES_THAN_HEADERS')
            rows.append(row)
        key=hashlib.sha256((os.path.normcase(str(source))+'\0'+digest+'\0'+day).encode('utf-8')).hexdigest()
        metadata={'complete':False,'origin':'legacy_csv','source':'legacy_csv','legacy_source_path':str(source),'legacy_sha256':digest,
            'legacy_encoding':encoding,'imported_at':_now(),'fetched_at':None,'quote_time':None,'historical_pit':False,
            'quote_basis':'legacy CSV snapshot; original quote time unknown','warnings':['历史CSV没有可验证的原获取/行情时间；不得当作完整网络成功或历史点时行情。']}
        with self._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                old=c.execute('SELECT * FROM runs WHERE import_key=?',(key,)).fetchone()
                if old is not None:
                    result=self._run(old);result['imported_existing']=True;c.commit();return result
                run_id=self._insert_run(c,day,None,'legacy_csv',key)
                result=self._finish(c,run_id,_normalize(rows),metadata,'partial');c.commit();result['imported_existing']=False;return result
            except BaseException:c.rollback();raise
