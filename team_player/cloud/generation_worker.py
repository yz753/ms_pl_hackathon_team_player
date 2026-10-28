"""Queue worker; generate with the exact local entry point, never analyse history."""
import hashlib,json,logging,tempfile,threading
from pathlib import Path
from contextlib import contextmanager
from team_player.cloud.generation_store import GenerationStore
from team_player.cloud.generation_spec import recording_name,validate_spec
from team_player.cloud.generate_match import generate

@contextmanager
def renewing_lease(blob):
    lease=blob.acquire_lease(lease_duration=60)
    stop=threading.Event();errors=[]
    def renew():
        while not stop.wait(20):
            try:lease.renew()
            except Exception as exc:errors.append(exc);return
    thread=threading.Thread(target=renew,daemon=True);thread.start()
    try:yield lease,errors
    finally:
        stop.set();thread.join(timeout=2)
        try:lease.release()
        except Exception:logging.exception('Could not release generation lease')

def process(job_id,store=None):
    store=store or GenerationStore();blob=store.blob(job_id)
    with renewing_lease(blob) as (lease,errors):
        doc=store.get(job_id)
        if doc.get('status')=='ready':return doc
        try:
            spec=validate_spec(doc['spec'])
            doc=store.put({**doc,'status':'running','error':None},lease)
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'match.jsonl.gz';result=generate(spec,path)
                if errors:raise RuntimeError('Generation lease lost')
                name=recording_name(spec);key=f'current/2026-27/{name}'
                with path.open('rb') as handle:
                    store.service.get_blob_client('tracking',key).upload_blob(handle,overwrite=True,
                        metadata={'season':'2026-27','generation_id':job_id,'sha256_positions':result['sha256_positions']})
            return store.put({**doc,'status':'ready','file':name,'result':result,
                'watch_url':'/?match='+name},lease)
        except Exception:
            logging.exception('Match generation failed: %s',job_id)
            store.put({**doc,'status':'failed','error':'Generation failed. Retry with the same settings; if it repeats, the deployment owner can inspect the worker logs.'},lease)
            raise
