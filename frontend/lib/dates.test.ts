import {expect,it} from 'vitest';
import {apiDate} from './dates';
it('treats legacy naive timestamps as UTC and preserves explicit offsets',()=>{
 expect(apiDate('2026-10-15T15:18:06.925046').toISOString()).toBe('2026-10-15T15:18:06.925Z');
 expect(apiDate('2026-10-15T23:18:06+08:00').toISOString()).toBe('2026-10-15T15:18:06.000Z');
 expect(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(apiDate('2026-10-15T15:18:06'))).toBe('23:18');
});
