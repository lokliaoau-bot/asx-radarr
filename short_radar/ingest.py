import csv, io, hashlib
from dataclasses import dataclass
from .models import Bar,ValidationError

COLUMNS=['market','symbol','name','sector','currency','session_date','open','high','low','close','volume','volume_unit','session_open_utc','session_close_utc','available_at','source','basis','close_definition','calendar_source','calendar_verified','demo']
OPTIONAL_COLUMNS=['incomplete','source_verified','availability_kind','adjustment_id','volume_basis']

@dataclass
class Preview:
    sha256: str
    mode: str
    rows: list
    issues: list
    total: int
    raw: str
    def summary(self):
        states={}
        for row in self.rows:states[row.quality]=states.get(row.quality,0)+1
        return {'sha256':self.sha256,'mode':self.mode,'total':self.total,'accepted':len(self.rows),'rejected':len(self.issues),'states':states,'issues':self.issues}

def preview_csv(text,now,mode='imported',calendar_overrides=None,allowed_markets=None):
    if len(text.encode('utf8'))>20*1024*1024:raise ValidationError('CSV exceeds 20 MiB')
    try:
        return _preview_csv(text,now,mode,calendar_overrides,allowed_markets)
    except csv.Error:
        raise ValidationError('CSV引号或字段格式有问题，请按导入模板检查后重试。') from None


def _preview_csv(text,now,mode,calendar_overrides,allowed_markets=None):
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff'),newline=''),strict=True)
    if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValidationError('duplicate or missing CSV header')
    if set(COLUMNS)-set(reader.fieldnames):raise ValidationError('missing CSV columns: '+','.join(sorted(set(COLUMNS)-set(reader.fieldnames))))
    if set(reader.fieldnames)-set(COLUMNS+OPTIONAL_COLUMNS):
        raise ValidationError('CSV有不认识的列名，请与导入模板逐字核对。')
    rows=[];issues=[];seen=set();total=0
    for line,raw in enumerate(reader,2):
        total+=1
        if total>50000:raise ValidationError('CSV row limit exceeded')
        if allowed_markets is not None and raw.get('market') not in allowed_markets:
            raise ValidationError('本版只导入澳股；文件中包含非AU市场，整份文件未写入。')
        try:
            if None in raw or any(x is None for x in raw.values()):raise ValidationError('CSV column count mismatch')
            row=Bar.parse(raw,now,mode,calendar_overrides)
            key=(row.symbol_id,row.source,row.session_date,row.basis,row.close_definition)
            if key in seen:raise ValidationError('duplicate instrument/session/source/basis/close_definition in file')
            seen.add(key);rows.append(row)
        except (ValidationError,ValueError,KeyError) as e:issues.append({'line':line,'reason':str(e)})
    return Preview(hashlib.sha256(text.encode('utf8')).hexdigest(),mode,rows,issues,total,text)

def to_csv(rows):
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=COLUMNS+OPTIONAL_COLUMNS,extrasaction='ignore',lineterminator='\n');w.writeheader();w.writerows(rows);return out.getvalue()
