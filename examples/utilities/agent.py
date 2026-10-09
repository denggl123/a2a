"""Useful deterministic JSON-stdio Agents; dependency image includes media tools."""
import base64,csv,io,json,math,re,sys,unicodedata,wave
from PIL import Image,ImageDraw

def file_result(raw,mime,**metadata):
    return {**metadata,'_a2n_files':[{'mime_type':mime,'base64':base64.b64encode(raw).decode()}]}

def run(skill,payload):
    if skill=='csv-profile':
        text=payload['csv'];rows=list(csv.DictReader(io.StringIO(text)))
        if len(rows)>10000:raise ValueError('CSV row limit')
        print(json.dumps({'event':'progress','delta':f'已读取 {len(rows)} 行；首行：'+json.dumps(rows[0] if rows else {},ensure_ascii=False)+'\n'},ensure_ascii=False),flush=True)
        columns={}
        for name in (rows[0] if rows else []):
            values=[r[name] for r in rows];numbers=[]
            for value in values:
                try:
                    number=float(value)
                    if math.isfinite(number):numbers.append(number)
                except (ValueError,TypeError):pass
            columns[name]={'missing':sum(v is None or not str(v).strip() for v in values),'numeric_count':len(numbers),
                           'numeric_sum':math.fsum(numbers) if numbers else None,'distinct':len(set(values))}
        return {'rows':len(rows),'columns':columns}
    if skill=='json-validate':
        parsed=json.loads(payload['json']);required=payload.get('required',[])
        missing=[key for key in required if not isinstance(parsed,dict) or key not in parsed]
        return {'valid_json':True,'required_fields_present':not missing,'missing':missing,'normalized':parsed}
    if skill=='text-normalize':
        normalized=re.sub(r'\s+',' ',unicodedata.normalize('NFKC',payload['text'])).strip()
        return {'text':normalized,'characters':len(normalized)}
    if skill=='python-transform':
        code='import re, unicodedata\ndef normalize_text(text):\n    return re.sub(r"\\s+", " ", unicodedata.normalize("NFKC", text)).strip()\n'
        return {'code':code,'function':'normalize_text','purpose':'Unicode NFKC 与连续空白归一化；由买方独立用例评审。'}
    if skill=='image-card':
        title=str(payload['title'])[:80];image=Image.new('RGB',(640,360),(20,36,60));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((24,24,616,336),radius=24,fill=(44,80,115));draw.text((52,140),title,fill='white',font_size=28)
        out=io.BytesIO();image.save(out,format='PNG');return file_result(out.getvalue(),'image/png',title=title)
    if skill=='audio-tone':
        frequency=float(payload.get('frequency',440));duration=float(payload.get('seconds',1))
        if not 100<=frequency<=2000 or not .1<=duration<=3:raise ValueError('Tone range')
        import struct
        out=io.BytesIO()
        with wave.open(out,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
            audio.writeframes(b''.join(struct.pack('<h',int(6000*math.sin(2*math.pi*frequency*i/16000))) for i in range(int(duration*16000))))
        return file_result(out.getvalue(),'audio/wav',frequency=frequency,seconds=duration)
    if skill=='document-render':
        # UTF-8 document with explicit structure, readable privately and as a public text preview.
        text='# '+str(payload['title'])+'\n\n'+'\n\n'.join(str(p) for p in payload['paragraphs'])+'\n'
        return file_result(text.encode(),'text/plain',text=text,paragraphs=len(payload['paragraphs']))
    if skill=='video-title':
        import subprocess,tempfile
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            path=tmp+'/title.mp4'
            subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=c=0x254660:s=320x180:d=1',
                '-vf','drawbox=x=20:y=20:w=280:h=140:color=0x497ca1:t=fill','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',path],check=True,timeout=8)
            return file_result(open(path,'rb').read(),'video/mp4',title=str(payload.get('title','Video card')),seconds=1)
    raise ValueError('Unsupported utility skill')

if __name__=='__main__':
    try:print(json.dumps(run(sys.argv[1],json.load(sys.stdin)),ensure_ascii=False,allow_nan=False))
    except Exception as exc:print(json.dumps({'error_type':type(exc).__name__}));sys.exit(1)
