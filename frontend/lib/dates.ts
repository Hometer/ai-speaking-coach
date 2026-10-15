// 兼容尚未刷新/旧服务返回的无偏移 UTC；日期本身（YYYY-MM-DD）不改。
export function apiDate(value:string):Date {
  return new Date(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)?`${value}Z`:value);
}
