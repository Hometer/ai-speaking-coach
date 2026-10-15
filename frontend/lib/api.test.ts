import {webcrypto} from 'node:crypto';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {createSession,pendingCreation,recoverPendingSubmissions,setInviteCode} from './api';

beforeEach(()=>{sessionStorage.clear();localStorage.clear();vi.stubGlobal('crypto',webcrypto);setInviteCode('fixture-a');});
afterEach(()=>{vi.unstubAllGlobals();});
const response=(data:unknown)=>new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});

it('replays the exact pending creation when precheck IDs change after a lost response',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(response({session_id:'only-session'}));
 vi.stubGlobal('fetch',fetcher);
 await expect(createSession('basic','standard',5,'job_interview',undefined,{selectionId:'selection',deviceCheckId:1})).rejects.toThrow('response lost');
 const original=await pendingCreation();expect(original?.device_check_id).toBe(1);
 await createSession('basic','standard',5,'job_interview',undefined,{selectionId:'new-selection',deviceCheckId:2});
 const first=JSON.parse(fetcher.mock.calls[0][1].body),second=JSON.parse(fetcher.mock.calls[1][1].body);
 expect(second).toEqual(first);expect(first.request_key).toBeTruthy();expect(await pendingCreation()).toBeNull();
});

it('keeps pending creations isolated by invited identity',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new Error('lost')).mockResolvedValueOnce(response({session_id:'b'}));vi.stubGlobal('fetch',fetcher);
 await expect(createSession('basic','standard')).rejects.toThrow();
 const pendingA=await pendingCreation();setInviteCode('fixture-b');
 expect(await pendingCreation()).toBeNull();await createSession('beginner','simple',10,'hotel_checkin');
 setInviteCode('fixture-a');expect((await pendingCreation())?.request_key).toBe(pendingA?.request_key);
});

it('registers the completed original operation after refresh, then stops recovering ordinary visits',async()=>{
 sessionStorage.setItem('ai_intent:turn:s',JSON.stringify({digest:'fixture',key:'original-key'}));
 const fetcher=vi.fn().mockResolvedValueOnce(response({operation_id:'saved-op',state:'done'})).mockResolvedValueOnce(response({state:'succeeded'}));vi.stubGlobal('fetch',fetcher);
 await recoverPendingSubmissions('s');
 expect(String(fetcher.mock.calls[0][0])).toContain('request_key=original-key');
 expect(fetcher.mock.calls[1][0]).toBe('/api/v1/sessions/s/recoveries');
 expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({operation_id:'saved-op'});
 expect(sessionStorage.getItem('ai_intent:turn:s')).toBeNull();
 await recoverPendingSubmissions('s');expect(fetcher).toHaveBeenCalledTimes(2);
});

it('preserves an unaccepted or disconnected submission key without retrying the model',async()=>{
 sessionStorage.setItem('ai_intent:drill:s',JSON.stringify({digest:'fixture',key:'pending'}));
 const fetcher=vi.fn().mockRejectedValue(new Error('offline'));vi.stubGlobal('fetch',fetcher);
 await recoverPendingSubmissions('s');expect(fetcher).toHaveBeenCalledTimes(1);
 expect(sessionStorage.getItem('ai_intent:drill:s')).toContain('pending');
});

it('coalesces concurrent creation submissions before any provider request',async()=>{
 const fetcher=vi.fn().mockImplementation(async()=>{await new Promise(resolve=>setTimeout(resolve,5));return response({session_id:'one'});});vi.stubGlobal('fetch',fetcher);
 const values=await Promise.all([createSession('basic','standard'),createSession('basic','standard')]);
 expect(values[0].session_id).toBe(values[1].session_id);expect(fetcher).toHaveBeenCalledTimes(1);
});
