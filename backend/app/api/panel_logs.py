"""Logs require an active owner session, correct origin and fresh password."""
import json
from typing import Literal
from fastapi import APIRouter,Request,Response
from pydantic import BaseModel,ConfigDict,Field,SecretStr

router=APIRouter(prefix='/api/panel/logs',tags=['private panel logs'])

class ClientError(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:Literal['vue','window','promise','save_readback']
    errorType:Literal['Error','TypeError','ReferenceError','RangeError','SyntaxError','URIError','EvalError','AggregateError','AbortError','UnknownError']
    version:str=Field(pattern=r'^\d+\.\d+\.\d+$',max_length=32)
    suppressed:int=Field(default=0,ge=0,le=1000000)

@router.post('/client',status_code=204)
async def client_error(payload:ClientError,request:Request):
    request.app.state.auth_service.require_admin(request)
    request.app.state.auth_service.check_origin(request)
    request.app.state.panel_logs.emit('frontend','ERROR',**payload.model_dump(),requestId=request.state.request_id)

class LogQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:SecretStr=Field(min_length=1,max_length=256)
    limit:int=Field(default=300,ge=1,le=500)

@router.post('/query')
async def query_logs(payload:LogQuery,request:Request,response:Response):
    auth=request.app.state.auth_service
    auth.require_owner(request);auth.check_origin(request)
    auth.verify_current_password(request,payload.password.get_secret_value())
    service=request.app.state.panel_logs
    service.emit('security','INFO',event='logs_viewed',requestId=request.state.request_id)
    response.headers['Cache-Control']='no-store'
    rows=service.read(payload.limit)
    with request.app.state.database._connect() as db:
        audits=db.execute('SELECT action,outcome,created_at,request_id FROM moderation_audit ORDER BY id DESC LIMIT 100').fetchall()
    for audit in audits:
        rows.append({'at':audit['created_at'],'kind':'audit','level':'INFO','action':audit['action'],
                     'outcome':audit['outcome'],'requestId':audit['request_id']})
    return {'available':service.available,'maxBytes':50*1024*1024,'dropped':service.dropped,
            'items':sorted(rows,key=lambda row:row.get('at',''),reverse=True)[:payload.limit]}
