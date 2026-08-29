// 小焦 · JS 插件运行器
// 用法:
//   node plugin_runner.js describe <插件路径>   -> 输出工具描述 JSON
//   node plugin_runner.js exec    <插件路径> <工具名> <参数JSON> -> 输出执行结果
const path = require('path');
const args = process.argv.slice(2);

function load(modPath) {
  const p = path.resolve(modPath);
  let mod;
  if (p.endsWith('.mjs') || p.endsWith('.js') && require('fs').existsSync(p + '.mjs')) {
    // ESM 加载（同步不友好，直接 require json）
    mod = require(p);
  } else {
    mod = require(p);
  }
  return mod && mod.__esModule && mod.default ? mod.default : (mod.default || mod);
}

(async () => {
  const cmd = args[0];
  try {
    if (cmd === 'describe') {
      const inst = load(args[1]);
      const desc = (typeof inst.getToolDescriptions === 'function') ? inst.getToolDescriptions() : [];
      process.stdout.write(JSON.stringify(desc));
    } else if (cmd === 'exec') {
      const inst = load(args[1]);
      const name = args[2];
      const params = JSON.parse(args[3] || '{}');
      const r = (typeof inst.execute === 'function') ? inst.execute(name, params) : null;
      const v = (r && typeof r.then === 'function') ? await r : r;
      process.stdout.write(String(v ?? ''));
    } else if (cmd === 'settings') {
      const inst = load(args[1]);
      const s = (typeof inst.getSettings === 'function') ? inst.getSettings() : [];
      process.stdout.write(JSON.stringify(s));
    } else {
      process.stdout.write('未知命令');
    }
  } catch (e) {
    process.stdout.write('JS插件错误:' + (e && e.message ? e.message : String(e)));
  }
})();
