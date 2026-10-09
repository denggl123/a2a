"""Inspect actual private artifacts; only bounded text and fresh PNG are public."""
from __future__ import annotations
import base64
import hashlib
import io
import wave
from a2n_sdk.assets import valid_ref
from .container_engine import image_id,run_json

DRIVER='''import base64,io,json,subprocess,sys,tempfile
j=json.load(sys.stdin);raw=base64.b64decode(j["base64"]);mime=j["mime_type"];out={}
if mime=="application/pdf":
 from pypdf import PdfReader
 reader=PdfReader(io.BytesIO(raw),strict=True);out={"pages":len(reader.pages),"text":"\\n".join((page.extract_text() or '')[:2048] for page in reader.pages[:2])[:2048]}
elif mime.startswith("video/"):
 with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
  path=tmp+"/input";open(path,"wb").write(raw)
  data=json.loads(subprocess.check_output(["ffprobe","-v","quiet","-show_format","-show_streams","-of","json",path],timeout=5))
  stream=next(s for s in data["streams"] if s["codec_type"]=="video")
  out={"width":stream["width"],"height":stream["height"],"duration":float(data["format"].get("duration",0))}
  try:
   png=subprocess.check_output(["ffmpeg","-loglevel","error","-threads","1","-filter_threads","1","-i",path,"-frames:v","1","-vf","scale=192:192:force_original_aspect_ratio=decrease","-threads","1","-f","image2pipe","-vcodec","png","-"],timeout=5)
   out["frame_png"]=base64.b64encode(png).decode()
  except subprocess.SubprocessError:out['thumbnail_state']='UNAVAILABLE'
print(json.dumps(out))
'''

class MediaInspector:
    def __init__(self,store,assets):self.store,self.assets=store,assets
    def configure(self,body):
        if not isinstance(body,dict) or set(body)!={"image"}:raise ValueError("INVALID_MEDIA_SETTINGS")
        row={"image":image_id(body["image"]),"parser":"a2n-media-metadata/1"}
        self.store.put("media_settings","current",row);return row
    def inspect(self,ref,trade_uid=None):
        if not valid_ref(ref):raise ValueError("INVALID_MEDIA_ASSET_REFERENCE")
        if not self.assets.book.owned(ref):
            if not trade_uid:raise ValueError("VERIFIED_DELIVERY_REQUIRED")
            ref=self.assets.fetch(trade_uid,ref["asset_id"],timeout_seconds=10)
        if ref['size']>8*1024**2:raise ValueError("MEDIA_INSPECTION_SIZE_LIMIT")
        raw=b''.join(self.assets.book.chunks(ref['asset_id']));mime=ref['mime_type']
        out={"mime_type":mime,"size":ref['size'],"artifact_sha256":ref['sha256']}
        if mime in {'image/png','image/jpeg','image/webp'}:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height>10000000:raise ValueError("MEDIA_PIXEL_LIMIT")
                image.load();out.update(width=image.width,height=image.height,format=image.format)
        elif mime in {'audio/wav','audio/x-wav'}:
            from PIL import Image,ImageDraw
            with wave.open(io.BytesIO(raw),'rb') as audio:
                rate,frames,channels,width=audio.getframerate(),audio.getnframes(),audio.getnchannels(),audio.getsampwidth()
                if not 1<=channels<=8 or width not in {1,2,4} or not rate:raise ValueError("UNSUPPORTED_AUDIO_ENCODING")
                out.update(duration=frames/rate,channels=channels,sample_rate=rate,frames=frames)
                canvas=Image.new('RGB',(192,80),(20,36,60));draw=ImageDraw.Draw(canvas)
                for x in range(192):
                    position=min(frames-1,int(frames*x/192))
                    if position<0:break
                    audio.setpos(position);data=audio.readframes(1)[:width]
                    level=abs((data[0]-128)/128) if width==1 else abs(int.from_bytes(data,'little',signed=True)/(2**(width*8-1)))
                    draw.line((x,40-int(level*36),x,40+int(level*36)),fill=(93,199,170))
                image=io.BytesIO();canvas.save(image,format='PNG');out['frame_png']=base64.b64encode(image.getvalue()).decode()
        elif mime in {'text/plain','text/markdown','application/json'}:
            text=raw.decode('utf-8',errors='replace');out.update(characters=len(text),text=text[:2048])
        elif mime=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            import zipfile,xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info=archive.getinfo('word/document.xml')
                if info.file_size>2*1024**2:raise ValueError("DOCUMENT_EXPANSION_LIMIT")
                root=ET.fromstring(archive.read(info));text=' '.join(e.text or '' for e in root.iter() if e.tag.endswith('}t'))
                out.update(text=text[:2048],characters=len(text))
        elif mime=='application/pdf' or mime.startswith('video/'):
            cfg=self.store.get('media_settings','current')
            if not cfg:raise ValueError("MEDIA_CONTAINER_NOT_CONFIGURED")
            out.update(run_json(cfg['image'],['python','-I','-c',DRIVER],{'base64':base64.b64encode(raw).decode(),'mime_type':mime},
                                timeout_ms=15000,memory_mib=512,input_limit=16*1024**2))
        else:raise ValueError("MEDIA_FORMAT_NOT_SUPPORTED")
        return out

    @staticmethod
    def thumbnail(metadata):
        value=metadata.get('frame_png')
        if not value:return None
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(value,validate=True))) as image:
            if image.width*image.height>100000:return None
            clean=Image.new('RGB',image.size);clean.paste(image.convert('RGB'))
            for size in (192,96,48):
                clean.thumbnail((size,size));out=io.BytesIO();clean.save(out,format='PNG');raw=out.getvalue()
                if len(raw)<=8192:return {'mime_type':'image/png','base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'width':clean.width,'height':clean.height}
