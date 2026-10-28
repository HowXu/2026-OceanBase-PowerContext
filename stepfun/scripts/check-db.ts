import { Database } from '../server/db';
const db = new Database();
try {
  await db.init();
  console.log(JSON.stringify({ mode: db.mode, version: db.version, collections: await db.list() }, null, 2));
} finally { await db.close(); }
