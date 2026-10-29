const POWERCONTEXT_URL = process.env.POWERCONTEXT_URL || 'http://127.0.0.1:8000';
const POWERCONTEXT_SCOPE_ID = process.env.POWERCONTEXT_SCOPE_ID || 'task-tracker-scope';
const POWERCONTEXT_AUTH_HEADER = process.env.POWERCONTEXT_AUTH_HEADER || '';

class PowerContextClient {
  constructor() {
    this.baseUrl = POWERCONTEXT_URL;
    this.scopeId = POWERCONTEXT_SCOPE_ID;
    this.authHeader = POWERCONTEXT_AUTH_HEADER;
  }

  async request(method, path, body = null) {
    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
      },
    };

    if (this.authHeader) {
      options.headers['Authorization'] = this.authHeader;
    }

    if (body) {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(`${this.baseUrl}${path}`, options);

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`PowerContext API error: ${response.status} - ${error}`);
    }

    if (response.status === 204) {
      return null;
    }

    return response.json();
  }

  async rememberMemory(kind, text, metadata = {}) {
    return this.request('POST', '/v1/memory/remember', {
      scope_id: this.scopeId,
      kind,
      text,
      metadata,
    });
  }

  async searchMemory(query, limit = 10) {
    return this.request('POST', '/v1/memory/search', {
      scope_id: this.scopeId,
      query,
      limit,
    });
  }

  async listMemories(filters = {}) {
    return this.request('POST', '/v1/atomic-memory/list', {
      scope_id: this.scopeId,
      ...filters,
    });
  }

  async createHandoff(workContract, progress, nextAction) {
    return this.request('POST', '/v1/work/handoffs/prepare-current', {
      scope_id: this.scopeId,
      work_contract: workContract,
      progress,
      next_action: nextAction,
    });
  }

  async recordOutcome(handoffId, status, summary) {
    return this.request('POST', '/v1/work/outcomes/record', {
      scope_id: this.scopeId,
      handoff_id: handoffId,
      status,
      summary,
    });
  }

  async prepareContext(sections = ['memory', 'handoff']) {
    return this.request('POST', '/v1/context/prepare', {
      scope_id: this.scopeId,
      sections,
    });
  }
}

module.exports = { PowerContextClient };
