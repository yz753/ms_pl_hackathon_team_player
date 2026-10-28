"""Website API for Azure generation. The web process never imports the simulator."""
import asyncio,os
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from team_player.cloud.generation_store import GenerationStore
from team_player.config.league import TEAMS
router=APIRouter()
class GenerationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    seed:int=Field(ge=0,le=4294967295)
    home:str|None=None
    away:str|None=None

def enabled():return os.environ.get('TEAM_PLAYER_GENERATION')=='azure'
def store(request):
    if not enabled():raise HTTPException(503,'Match generation is not deployed yet')
    if not hasattr(request.app.state,'generation_store'):request.app.state.generation_store=GenerationStore()
    return request.app.state.generation_store

def public(doc):
    # Status never includes simulator profiles, credentials or exception traces.
    result={k:doc[k] for k in ('status','created_at','updated_at','file','watch_url','error') if k in doc}
    result['spec']={k:doc['spec'][k] for k in ('id','season','seed','home','away','duration_s','sample_hz')}
    return result

@router.get('/api/generation')
async def configuration():return {'enabled':enabled(),'teams':list(TEAMS),'season':'2026-27','duration_s':600}

@router.post('/api/generation',status_code=202)
async def submit(body:GenerationRequest,request:Request):
    try:return public(await asyncio.to_thread(store(request).submit,body.seed,body.home,body.away))
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'Azure generation queue is unavailable. Please retry.') from exc

async def document(request,job):
    from azure.core.exceptions import ResourceNotFoundError
    try:return await asyncio.to_thread(store(request).get,job)
    except (ResourceNotFoundError,ValueError) as exc:raise HTTPException(404,'Unknown generation request') from exc
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'Azure generation status is temporarily unavailable') from exc

@router.get('/api/generation/{job}')
async def status(job:str,request:Request):return public(await document(request,job))

