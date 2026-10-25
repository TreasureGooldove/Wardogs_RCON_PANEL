"""Bounded private operational logs; no request bodies, cookies or query strings."""
from datetime import UTC, datetime
import json
import logging
from logging.handlers import QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import traceback
from urllib.parse import urlsplit, urlunsplit


class _PrivateFile(RotatingFileHandler):
    def _open(self):
        fd=os.open(self.baseFilename,os.O_APPEND|os.O_CREAT|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        return os.fdopen(fd,'a',encoding='utf-8')

    def emit(self, record):
        try: super().emit(record)
        except (OSError,ValueError): self.failed()

    def handleError(self, record):
        self.failed()


class _RuntimeCapture(logging.Handler):
    def __init__(self, service):
        super().__init__(logging.INFO); self.service=service

    def emit(self, record):
        try:
            details={'logger':record.name}
            if record.exc_info and record.exc_info[0]:
                details['errorType']=record.exc_info[0].__name__
                details['frames']=[f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(record.exc_info[2])[-12:]]
            elif record.name=='asyncio':
                details['message']='background_task_state'
            else:
                details['message']=record.getMessage()
            self.service.emit('runtime',record.levelname,**details)
        except Exception:
            self.service.dropped+=1


class PanelLogs:
    def __init__(self, data_directory, secrets=None):
        self.root=Path(data_directory)/'panel-logs'
        self.secrets=secrets or (lambda:[])
        self.queue=queue.Queue(maxsize=4096)
        self.available=False; self.dropped=0; self.listener=None; self.capture=None; self.sink=None
        self.levels={}

    def clean(self, value):
        if isinstance(value,dict):
            return {key:'[REDACTED]' if re.search(r'password|token|bearer|secret|cookie|authorization|api.?key',key,re.I) else self.clean(item) for key,item in value.items()}
        if isinstance(value,(tuple,list)): return [self.clean(item) for item in value]
        if not isinstance(value,str): return value
        for secret in self.secrets():
            if secret and len(str(secret))>=6: value=value.replace(str(secret),'[REDACTED]')
        value=re.sub(r'(?i)\bBearer\s+[^\s,;]+','Bearer [REDACTED]',value)
        value=re.sub(r'(?i)\b(password|token|secret|api[_-]?key|cookie|authorization)\s*[:=]\s*(?:"[^"]*"|\'[^\']*\'|[^\s,;}]+)',r'\1=[REDACTED]',value)
        def url(match):
            try:
                parsed=urlsplit(match[0]);host=parsed.netloc.rsplit('@',1)[-1]
                return urlunsplit((parsed.scheme,host,parsed.path,'',''))
            except ValueError: return '[URL]'
        return re.sub(r'https?://[^\s"\'<>]+',url,value)[:2048]

    def start(self):
        try:
            if self.root.is_symlink(): raise OSError('unsafe log directory')
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.chmod(self.root,0o700)
            if any(path.is_symlink() for path in self.root.glob('panel.jsonl*')): raise OSError('unsafe log file')
            self.sink=_PrivateFile(self.root/'panel.jsonl',maxBytes=5*1024*1024,backupCount=9,encoding='utf-8')
            self.sink.failed=lambda:setattr(self,'available',False)
            self.sink.setFormatter(logging.Formatter('%(message)s'))
            self.listener=QueueListener(self.queue,self.sink);self.listener.start()
            self.available=True; self.capture=_RuntimeCapture(self)
            for name in ('wardogs','app','uvicorn.error','asyncio'):
                logger=logging.getLogger(name);self.levels[name]=logger.level
                if logger.getEffectiveLevel()>logging.INFO: logger.setLevel(logging.INFO)
                logger.addHandler(self.capture)
            self.emit('system','INFO',event='panel_started')
        except OSError:
            self.available=False

    def emit(self, kind, level='INFO', **details):
        if not self.available: return
        try: value=self.clean({'at':datetime.now(UTC).isoformat(),'kind':kind,'level':level,**details})
        except Exception:
            self.dropped+=1;return
        if self.dropped: value['droppedBefore']=self.dropped
        record=logging.LogRecord('panel.private',logging.INFO,'',0,json.dumps(value,ensure_ascii=False),(),None)
        try: self.queue.put_nowait(record);self.dropped=0
        except queue.Full: self.dropped+=1

    def exception(self, error, **details):
        self.emit('error','ERROR',errorType=type(error).__name__,
            frames=[f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(error.__traceback__)[-12:]],**details)

    def read(self, limit=300):
        rows=[]
        for number in range(10):
            path=self.root/('panel.jsonl'+('' if number==0 else '.'+str(number)))
            if path.is_symlink(): continue
            try:
                with path.open('rb') as handle:
                    size=handle.seek(0,2);handle.seek(max(0,size-1048576))
                    lines=handle.read(1048576).splitlines()
                for line in reversed(lines):
                    try:
                        row=json.loads(line)
                        if isinstance(row,dict): rows.append(self.clean(row))
                    except (ValueError,UnicodeError): continue
                    if len(rows)>=limit: return rows
            except OSError: continue
        return rows

    def close(self):
        self.emit('system','INFO',event='panel_stopped')
        self.available=False
        if self.capture:
            for name in ('wardogs','app','uvicorn.error','asyncio'):
                logger=logging.getLogger(name);logger.removeHandler(self.capture)
                logger.setLevel(self.levels.get(name,logger.level))
        if self.listener:
            if self.queue.full():
                try: self.queue.get_nowait();self.queue.task_done()
                except queue.Empty: pass
            self.listener.stop()
        if self.sink: self.sink.close()
