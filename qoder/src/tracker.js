import { AppError, decodeRecord } from './powercontext.js';
export const TASK_KIND = 'task-tracker.task.v1';
export const HANDOFF_KIND = 'task-tracker.handoff.v1';
const now = () => new Date().toISOString();
const fail = message => { throw new AppError(400, 'invalid_input', message); };

function text(value, name, max, required = false) {
  if (typeof value !== 'string' || value.trim().length > max || (required && !value.trim())) fail(`${name}${required ? '不能为空，且' : ''}必须为不超过 ${max} 字的文本。`);
  return value.trim();
}
function taskInput(input) {
  const title = text(input.title, '标题', 120, true);
  const description = text(input.description ?? '', '描述', 1000);
  const priority = input.priority ?? 'medium';
  if (!['low', 'medium', 'high'].includes(priority)) fail('优先级无效。');
  const progress = input.progress ?? 0;
  if (!Number.isInteger(progress) || progress < 0 || progress > 100) fail('进度必须为 0–100 的整数。');
  const deadline = input.deadline || null;
  if (deadline && (typeof deadline !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(deadline) || Number.isNaN(Date.parse(deadline)) || new Date(deadline).toISOString().slice(0, 10) !== deadline)) fail('截止日期无效。');
  return { title, description, priority, deadline, progress, status: progress === 100 ? 'done' : progress > 0 ? 'in_progress' : 'todo' };
}
export class Tracker {
  constructor(client) { this.client = client; }
  async list(kind) {
    return (await this.client.list(kind)).map(item => decodeRecord(item.text, item.artifact, kind))
      .filter(item => !item.deleted).sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
  }
  tasks() { return this.list(TASK_KIND); }
  handoffs() { return this.list(HANDOFF_KIND); }
  async createTask(input) {
    const timestamp = now();
    return this.client.create(TASK_KIND, { schema: TASK_KIND, ...taskInput(input), createdAt: timestamp, updatedAt: timestamp });
  }
  async updateTask(id, input, deleted = false) {
    const { record, etag } = await this.client.get(TASK_KIND, id);
    if (!Number.isInteger(input.revision) || input.revision < 1) fail('请提供记录版本并刷新后重试。');
    if (record.revision !== input.revision) throw new AppError(409, 'revision_conflict', '记录已更新，请刷新后重新操作。');
    return this.client.replace(TASK_KIND, { ...record, ...taskInput({ ...record, ...input }), deleted, updatedAt: now() }, etag);
  }
  async createHandoff(input) {
    if (typeof input.taskId !== 'string' || !input.taskId) fail('请选择任务。');
    const { record: task } = await this.client.get(TASK_KIND, input.taskId);
    const summary = text(input.summary ?? '', '工作摘要', 1000);
    const nextAction = text(input.nextAction, '下一步行动', 500, true);
    const prepared = await this.client.prepareHandoff(task, summary, nextAction);
    // The prepared Handoff is temporary. Persist the reusable snapshot separately as Memory.
    return this.client.create(HANDOFF_KIND, { schema: HANDOFF_KIND,
      taskId: task.id, taskTitle: task.title, summary, nextAction, progress: task.progress,
      status: task.status, taskRevision: task.revision, boundary: prepared.boundary, createdAt: now() });
  }
  context(input) { return this.client.prepareContext(text(input.query, '检索内容', 200, true)); }
  async summary() {
    const tasks = await this.tasks();
    const count = status => tasks.filter(task => task.status === status).length;
    return { total: tasks.length, todo: count('todo'), inProgress: count('in_progress'), done: count('done'),
      highPriority: tasks.filter(task => task.priority === 'high').length,
      averageProgress: tasks.length ? Math.round(tasks.reduce((sum, task) => sum + task.progress, 0) / tasks.length) : 0,
      overdue: tasks.filter(task => task.deadline && task.deadline < now().slice(0, 10) && task.status !== 'done').length };
  }
}
