const express = require('express');
const path = require('path');
const { PowerContextClient } = require('./powercontext');

const app = express();
const PORT = process.env.PORT || 3002;

const powerContext = new PowerContextClient();

// In-memory task store
let tasks = [];
let taskIdCounter = 1;
let handoffs = [];

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// API Routes

// Get all tasks
app.get('/api/tasks', (req, res) => {
  res.json({ tasks });
});

// Create a new task
app.post('/api/tasks', async (req, res) => {
  const { title, description, priority = 'medium', deadline } = req.body;

  if (!title) {
    return res.status(400).json({ error: '标题不能为空' });
  }

  const task = {
    id: taskIdCounter++,
    title,
    description: description || '',
    priority,
    deadline: deadline || null,
    progress: 0,
    status: 'todo',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  tasks.push(task);

  // Save to PowerContext Memory
  try {
    await powerContext.rememberMemory('decision', `创建任务：${task.title}`, {
      task_id: task.id,
      task_title: task.title,
      status: task.status,
      priority: task.priority,
    });
  } catch (error) {
    console.error('PowerContext Memory 保存失败:', error.message);
  }

  res.status(201).json(task);
});

// Update task
app.put('/api/tasks/:id', async (req, res) => {
  const taskId = parseInt(req.params.id);
  const { progress, status, title, description, priority, deadline } = req.body;

  const task = tasks.find(t => t.id === taskId);
  if (!task) {
    return res.status(404).json({ error: '任务不存在' });
  }

  if (title !== undefined) task.title = title;
  if (description !== undefined) task.description = description;
  if (priority !== undefined) task.priority = priority;
  if (deadline !== undefined) task.deadline = deadline;
  if (progress !== undefined) {
    task.progress = Math.max(0, Math.min(100, progress));
    if (task.progress === 100) task.status = 'done';
    else if (task.progress > 0 && task.status === 'todo') task.status = 'in_progress';
  }
  if (status !== undefined) task.status = status;
  task.updatedAt = new Date().toISOString();

  // Update PowerContext Memory
  try {
    await powerContext.rememberMemory('decision',
      `更新任务：${task.title} - 进度：${task.progress}%，状态：${task.status}`,
      {
        task_id: task.id,
        task_title: task.title,
        status: task.status,
        progress: task.progress,
      }
    );
  } catch (error) {
    console.error('PowerContext Memory 更新失败:', error.message);
  }

  res.json(task);
});

// Delete task
app.delete('/api/tasks/:id', async (req, res) => {
  const taskId = parseInt(req.params.id);
  const taskIndex = tasks.findIndex(t => t.id === taskId);

  if (taskIndex === -1) {
    return res.status(404).json({ error: '任务不存在' });
  }

  const task = tasks[taskIndex];
  tasks.splice(taskIndex, 1);

  // Record deletion in PowerContext
  try {
    await powerContext.rememberMemory('decision', `删除任务：${task.title}`, {
      task_id: task.id,
      task_title: task.title,
      action: 'deleted',
    });
  } catch (error) {
    console.error('PowerContext Memory 删除记录失败:', error.message);
  }

  res.json({ message: '任务已删除' });
});

// Get task by ID
app.get('/api/tasks/:id', (req, res) => {
  const taskId = parseInt(req.params.id);
  const task = tasks.find(t => t.id === taskId);

  if (!task) {
    return res.status(404).json({ error: '任务不存在' });
  }

  res.json(task);
});

// Create handoff
app.post('/api/handoff', async (req, res) => {
  const { taskId, summary, nextAction } = req.body;

  const task = tasks.find(t => t.id === taskId);
  if (!task) {
    return res.status(404).json({ error: '任务不存在' });
  }

  const handoff = {
    id: handoffs.length + 1,
    taskId,
    taskTitle: task.title,
    summary: summary || '',
    nextAction: nextAction || '',
    progress: task.progress,
    status: task.status,
    createdAt: new Date().toISOString(),
  };

  handoffs.push(handoff);

  // Save handoff to PowerContext
  try {
    await powerContext.createHandoff(
      `任务：${task.title}`,
      `进度：${task.progress}%，状态：${task.status}`,
      nextAction || '继续完成任务'
    );
  } catch (error) {
    console.error('PowerContext Handoff 保存失败:', error.message);
  }

  res.status(201).json(handoff);
});

// Get all handoffs
app.get('/api/handoff', (req, res) => {
  res.json({ handoffs });
});

// Get summary
app.get('/api/summary', (req, res) => {
  const summary = {
    total: tasks.length,
    todo: tasks.filter(t => t.status === 'todo').length,
    inProgress: tasks.filter(t => t.status === 'in_progress').length,
    done: tasks.filter(t => t.status === 'done').length,
    highPriority: tasks.filter(t => t.priority === 'high').length,
    averageProgress: tasks.length > 0
      ? Math.round(tasks.reduce((sum, t) => sum + t.progress, 0) / tasks.length)
      : 0,
    overdue: tasks.filter(t => t.deadline && new Date(t.deadline) < new Date() && t.status !== 'done').length,
  };
  res.json(summary);
});

// PowerContext health check
app.get('/api/powercontext/health', async (req, res) => {
  try {
    const result = await powerContext.prepareContext(['memory']);
    res.json({ status: 'connected', scopeId: powerContext.scopeId });
  } catch (error) {
    res.status(503).json({ status: 'disconnected', error: error.message });
  }
});

// Serve static files (disable index.html redirect)
app.use(express.static(path.join(__dirname, '../public'), { index: false }));

// Serve pages
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/dashboard.html'));
});

app.get('/tasks', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/tasks.html'));
});

app.get('/handoff', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/handoff.html'));
});

// Handle client-side routing
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/dashboard.html'));
});

app.listen(PORT, () => {
  console.log(`Task Tracker running at http://localhost:${PORT}`);
  console.log(`PowerContext URL: ${powerContext.baseUrl}`);
  console.log(`PowerContext Scope: ${powerContext.scopeId}`);
});
