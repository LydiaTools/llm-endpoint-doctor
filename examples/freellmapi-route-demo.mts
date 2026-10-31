/**
 * Optional integration fixture: run an explicitly selected FreeLLMAPI source
 * tree with an in-memory database and a fully synthetic Groq fetch response.
 * No account, provider key, model call, or existing database is used.
 * See docs/chat-vs-tools.md before running.
 */
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [rootArg, doctorArg] = process.argv.slice(2);
if (!rootArg || !doctorArg || process.argv.length !== 4) {
  console.error('Usage: tsx freellmapi-route-demo.mts <freellmapi-source-dir> <llm-doctor-executable>');
  process.exit(2);
}
const root = path.resolve(rootArg), doctor = path.resolve(doctorArg);
const isolatedHome = await mkdtemp(path.join(tmpdir(), 'doctor-router-demo-'));
// Do not pass the caller's provider keys, proxy configuration, or app settings
// into the third-party source tree or the child process.
const allowedEnv = new Set(['PATH', 'TMPDIR', 'TMP', 'TEMP', 'SYSTEMROOT', 'SystemRoot', 'COMSPEC', 'ComSpec']);
for (const key of Object.keys(process.env)) if (!allowedEnv.has(key)) delete process.env[key];
Object.assign(process.env, {
  HOME: isolatedHome, USERPROFILE: isolatedHome, NODE_ENV: 'test', CI: '1',
  ENCRYPTION_KEY: '0'.repeat(64), NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost',
});

const calls: { model: string; tools: boolean }[] = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  if (url !== 'https://api.groq.com/openai/v1/chat/completions') throw new Error('Unexpected fetch blocked by fixture');
  const body = JSON.parse(String(init?.body));
  const tools = Array.isArray(body.tools) && body.tools.length > 0;
  calls.push({ model: body.model, tools });
  const message = tools
    ? { role: 'assistant', content: null, tool_calls: [{ id: 'call_fixture', type: 'function', function: { name: 'endpoint_doctor_probe', arguments: '{"status":"ok"}' } }] }
    : { role: 'assistant', content: 'synthetic-ok' };
  return new Response(JSON.stringify({
    id: 'synthetic-response', object: 'chat.completion', created: 0, model: body.model,
    choices: [{ index: 0, message, finish_reason: tools ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
const load = (relative: string) => import(pathToFileURL(path.join(root, relative)).href);
let server: import('node:http').Server | undefined;
let db: any;
try {
  const { initDb, getDb, getUnifiedApiKey } = await load('server/src/db/index.ts');
  const { nodeSqliteFactory } = await load('server/src/db/node-sqlite.ts');
  const { encrypt } = await load('server/src/lib/crypto.ts');
  const { routeRequest, setRoutingStrategy } = await load('server/src/services/router.ts');
  const { createApp } = await load('server/src/app.ts');
  initDb(':memory:', { factory: nodeSqliteFactory, ensureDir: false });
  db = getDb();
  setRoutingStrategy('priority');
  db.prepare("DELETE FROM settings WHERE key = 'active_profile_id'").run();
  const secret = encrypt('fixture-only-groq-key');
  db.prepare("INSERT INTO api_keys (platform,label,encrypted_key,iv,auth_tag,status,enabled) VALUES ('groq','isolated-fixture',?,?,?,'healthy',1)").run(secret.encrypted, secret.iv, secret.authTag);
  const models = db.prepare("SELECT id,model_id FROM models WHERE platform='groq' AND enabled=1 ORDER BY id LIMIT 2").all() as {id:number;model_id:string}[];
  if (models.length !== 2) throw new Error('Expected two enabled seeded Groq models at the tested source revision');
  db.prepare('UPDATE fallback_config SET enabled=0').run();
  db.prepare('UPDATE models SET supports_tools=0 WHERE id=?').run(models[0].id);
  db.prepare('UPDATE models SET supports_tools=1 WHERE id=?').run(models[1].id);
  for (let i=0; i<models.length; i++) db.prepare('INSERT INTO fallback_config (model_db_id,priority,enabled) VALUES (?,?,1) ON CONFLICT(model_db_id) DO UPDATE SET priority=excluded.priority,enabled=1').run(models[i].id, i);
  const chatRoute=routeRequest(1000), toolRoute=routeRequest(1000,undefined,undefined,false,true);
  if (chatRoute.modelId === toolRoute.modelId) throw new Error('Fixture did not select two different model labels');
  server = createApp().listen(0, '127.0.0.1');
  await new Promise<void>((resolve,reject) => { server!.once('listening',resolve);server!.once('error',reject); });
  const addr = server.address();
  if (!addr || typeof addr === 'string') throw new Error('No loopback address');
  const key = getUnifiedApiKey();
  const args = ['--base-url', `http://127.0.0.1:${addr.port}/v1`, '--model', 'auto', '--api-key-env', 'FIXTURE_ROUTER_KEY', '--check-tool-calling', '--compare-route-headers'];
  const child = spawn(doctor, args, { env: { ...process.env, FIXTURE_ROUTER_KEY: key }, stdio: ['ignore','pipe','pipe'] });
  let stdout='', stderr='';
  child.stdout.on('data', x => stdout += x); child.stderr.on('data', x => stderr += x);
  const timer = setTimeout(() => child.kill('SIGTERM'), 30_000);
  const code = await new Promise<number|null>((resolve,reject) => { child.once('error',reject);child.once('close',resolve); }).finally(() => clearTimeout(timer));
  if (code !== 0 || !stdout.includes('header values differed') || !stdout.includes('[PASS] chat completion') || !stdout.includes('[PASS] tool calling')) throw new Error('Doctor returned an unexpected result; inspect the test configuration.');
  if (calls.length !== 2 || calls[0].tools || !calls[1].tools || calls[0].model === calls[1].model) throw new Error('Unexpected synthetic provider calls.');
  if (stdout.includes(key) || calls.some(c => stdout.includes(c.model)) || stderr.includes(key)) throw new Error('Unexpected fixture key or route label in doctor output.');
  console.log('Actual FreeLLMAPI app + installed doctor; in-memory DB; two synthetic provider responses.');
  console.log(stdout);
  console.log('Verified: two provider fetches were intercepted; route labels and fixture key were not printed by the doctor.');
} finally {
  if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
  db?.close?.();
  globalThis.fetch = originalFetch;
  await rm(isolatedHome, { recursive:true, force:true });
}
