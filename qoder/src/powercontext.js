export class AppError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

export class PowerContextClient {
  constructor({ baseUrl = '', scopeId = '', authHeader = '', timeoutMs = 10000 } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.scopeId = scopeId; this.authHeader = authHeader; this.timeoutMs = timeoutMs;
  }
  async request(method, path, body, extraHeaders = {}) {
    if (!this.baseUrl || !this.scopeId) throw new AppError(503, 'not_configured', '尚未配置 PowerContext 地址或有效 Scope ID，请按部署说明完成配置。');
    const headers = { 'Content-Type': 'application/json', ...extraHeaders };
    if (this.authHeader) headers.Authorization = this.authHeader;
    let response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, { method, headers,
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(this.timeoutMs) });
    } catch { throw new AppError(503, 'upstream_unavailable', '无法连接 PowerContext，操作未确认保存。请检查服务后重试。'); }
    if (!response.ok) {
      // Do not expose upstream deployment details or credentials to public visitors.
      if ([409, 412].includes(response.status)) throw new AppError(409, 'revision_conflict', '记录已被其他会话修改，请刷新后重新操作。');
      if (response.status === 404) throw new AppError(404, 'not_found', '记录或配置的 Scope 不存在。');
      throw new AppError(502, 'upstream_rejected', `PowerContext 拒绝请求（HTTP ${response.status}），未确认保存。`);
    }
    let data;
    try { data = response.status === 204 ? null : await response.json(); }
    catch { throw new AppError(502, 'invalid_upstream_response', 'PowerContext 返回了无效响应。'); }
    return { data, etag: response.headers.get('etag') };
  }
  get artifactBase() { return `/v1/scopes/${encodeURIComponent(this.scopeId)}/artifacts`; }
  async list(kind) {
    const items = [], cursors = new Set();
    let cursor;
    do {
      const { data } = await this.request('POST', '/v1/atomic-memory/list', {
        scope_id: this.scopeId, kind, states: ['active'], limit: 100, ...(cursor ? { cursor } : {}) });
      if (!Array.isArray(data?.items)) throw new AppError(502, 'invalid_upstream_response', 'PowerContext 记忆列表格式不正确。');
      items.push(...data.items); cursor = data.next_cursor;
      if (cursor && (cursors.has(cursor) || cursors.size >= 1000)) throw new AppError(502, 'invalid_pagination', '记忆分页异常，请检查 PowerContext 服务。');
      if (cursor) cursors.add(cursor);
    } while (cursor);
    return items;
  }
  content(kind, record) {
    const text = JSON.stringify(record);
    if (new TextEncoder().encode(text).byteLength > 8192) throw new AppError(400, 'record_too_large', '记录超过 8192 字节，请缩短内容。');
    return { schema: 'powercontext.atomic-memory.v1', kind, text };
  }
  async create(kind, record) {
    const { data } = await this.request('POST', this.artifactBase, { family: 'atomic-memory', content: this.content(kind, record) });
    if (!data?.artifact_id || !Number.isInteger(data.revision)) throw new AppError(502, 'invalid_upstream_response', '服务未返回保存凭据，请刷新核对记录。');
    return { ...record, id: data.artifact_id, revision: data.revision };
  }
  async get(kind, id) {
    const result = await this.request('GET', `${this.artifactBase}/atomic-memory/${encodeURIComponent(id)}`);
    if (result.data?.content?.kind !== kind) throw new AppError(404, 'not_found', '记录不存在。');
    const record = decodeRecord(result.data.content.text, { artifact_id: id, revision: result.data.revision }, kind);
    if (record.deleted) throw new AppError(404, 'not_found', '记录已删除。');
    return { record, etag: result.etag };
  }
  async replace(kind, record, etag) {
    if (!etag) throw new AppError(502, 'missing_validator', '服务未返回版本校验信息，无法安全保存。');
    const { id, revision, ...value } = record;
    const { data } = await this.request('PUT', `${this.artifactBase}/atomic-memory/${encodeURIComponent(id)}`, {
      content: this.content(kind, value) }, { 'If-Match': etag });
    if (!Number.isInteger(data?.revision)) throw new AppError(502, 'invalid_upstream_response', '服务未返回更新凭据，请刷新核对记录。');
    return { ...value, id, revision: data.revision };
  }
  async prepareHandoff(task, summary, nextAction) {
    const claim = text => ({ text, basis: 'declared', evidence: [] });
    const { data } = await this.request('POST', '/v1/work/handoffs/prepare-current', {
      scope_id: this.scopeId, source_id: `task-tracker-handoff-${crypto.randomUUID()}`,
      handoff: { schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input',
        objective: `继续任务：${task.title}`,
        state: [claim(`任务 ID：${task.id}；进度：${task.progress}%；状态：${task.status}。${summary}`)],
        disposition: task.status === 'done' ? 'complete' : 'continuable',
        next_action: task.status === 'done' ? null : claim(nextAction), omissions: [] } });
    if (!data?.boundary || !data?.handoff) throw new AppError(502, 'invalid_handoff', '未取得有效的 PowerContext 交接凭据。');
    return data;
  }
  async prepareContext(query) {
    const { data } = await this.request('POST', '/v1/context/prepare', { scope_id: this.scopeId, query, max_bytes: 8000 });
    return data;
  }
}

export function decodeRecord(text, artifact, kind) {
  let value;
  try { value = JSON.parse(text); } catch { /* invalid data is reported below */ }
  if (!value || value.schema !== kind || !artifact?.artifact_id || !Number.isInteger(artifact.revision)) throw new AppError(502, 'invalid_record', '发现无法读取的任务记忆，请检查该 Scope 中的记录。');
  return { ...value, id: artifact.artifact_id, revision: artifact.revision };
}
