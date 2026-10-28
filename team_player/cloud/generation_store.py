"""Azure-only durable job state shared by the website and the Function worker."""
import json,os,re,time
from datetime import datetime,timezone
from team_player.cloud.generation_spec import make_spec
CONTAINER='generation'
QUEUE='team-player-generation'
ID=re.compile(r'^[0-9a-f]{32}$')
def timestamp():return datetime.now(timezone.utc).isoformat()

class GenerationStore:
    def __init__(self,service=None,queue=None):
        if service is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
            credential=DefaultAzureCredential()
            account=os.environ['TEAM_PLAYER_BLOB_ACCOUNT_URL']
            service=BlobServiceClient(account,credential=credential)
        self.service=service;self._queue=queue
    def blob(self,job):
        if not ID.fullmatch(job):raise ValueError('Invalid generation ID')
        return self.service.get_blob_client(CONTAINER,f'jobs/{job}.json')
    def get(self,job):return json.loads(self.blob(job).download_blob().readall())
    def put(self,doc,lease=None):
        doc={**doc,'updated_at':timestamp()}
        self.blob(doc['spec']['id']).upload_blob(json.dumps(doc),overwrite=True,lease=lease)
        return doc
    def queue(self):
        if self._queue is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.queue import QueueClient,TextBase64EncodePolicy
            url=os.environ['TEAM_PLAYER_BLOB_ACCOUNT_URL'].replace('.blob.', '.queue.')
            self._queue=QueueClient(url,QUEUE,credential=DefaultAzureCredential(),message_encode_policy=TextBase64EncodePolicy())
        return self._queue
    def submit(self,seed,home=None,away=None):
        from azure.core.exceptions import ResourceExistsError
        spec=make_spec(seed,home,away);doc={'spec':spec,'status':'queued','created_at':timestamp(),'updated_at':timestamp()}
        try:self.blob(spec['id']).upload_blob(json.dumps(doc),overwrite=False)
        except ResourceExistsError:
            doc=self.get(spec['id'])
            if doc['status'] in ('ready','running'):return doc
            if doc['status']=='failed':doc=self.put({**doc,'status':'queued','error':None})
        # A retry after enqueue failure safely resends the same ID; worker is idempotent.
        self.queue().send_message(spec['id'])
        return doc
