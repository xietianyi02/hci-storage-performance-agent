const pendingReads = new Map<string, Promise<unknown>>();

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (body !== undefined || signal) return request<T>(path, body, signal);
  const pending = pendingReads.get(path);
  if (pending) return pending as Promise<T>;
  const reading = request<T>(path);
  pendingReads.set(path, reading);
  try { return await reading; }
  finally { if (pendingReads.get(path) === reading) pendingReads.delete(path); }
}

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    signal,
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `服务请求失败 (${response.status})`;
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.slice(0,3).map((item:{loc?:unknown[];msg?:string})=>`${item.loc?.filter(part=>part!=='body').join('.') || '数据'}：${item.msg || '格式错误'}`).join('；') : message; } catch { /* response may be text */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
