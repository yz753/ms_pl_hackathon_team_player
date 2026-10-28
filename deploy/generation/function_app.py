import azure.functions as func
from team_player.cloud.generation_worker import process
app=func.FunctionApp()
@app.queue_trigger(arg_name='request',queue_name='team-player-generation',connection='AzureWebJobsStorage')
def generate_match(request:func.QueueMessage):
    process(request.get_body().decode('utf-8'))

@app.queue_trigger(arg_name='request',queue_name='team-player-generation-poison',connection='AzureWebJobsStorage')
def failed_generation(request:func.QueueMessage):
    from team_player.cloud.generation_store import GenerationStore
    from team_player.cloud.generation_worker import renewing_lease
    store=GenerationStore();job=request.get_body().decode('utf-8')
    with renewing_lease(store.blob(job)) as (lease,errors):
        doc=store.get(job)
        if doc.get('status')!='ready':
            store.put({**doc,'status':'failed','error':'Azure could not complete this match after retries. Try again, or contact the deployment owner.'},lease)
