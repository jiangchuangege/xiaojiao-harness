// 小焦 · DSH 社区插件移植：dsh-netdoctor（网络诊断）
// 来源: github.com/TYEclipse/dsh-netdoctor —— 零依赖只读探针
// 原插件为 DSH(cordis) 版本，这里移植成小焦独立 JS 插件（module.exports 接口）。
const dns = require('dns');
const net = require('net');

async function httpGet(url, timeoutMs) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs || 8000);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { 'user-agent': 'xiaojiao-netdoctor/0.1' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.text();
  } finally { clearTimeout(t); }
}

module.exports = {
  getToolDescriptions() {
    return [
      { name: 'net_ip', description: '查询本机公网IP，可选含归属地/ISP/时区等', parameters: { type: 'object', properties: { geo: { type: 'boolean', description: '是否附带归属地(默认true)' } } } },
      { name: 'net_dns', description: 'DNS 解析域名（A 记录 + MX 邮件记录）', parameters: { type: 'object', properties: { host: { type: 'string', description: '域名，如 baidu.com' } }, required: ['host'] } },
      { name: 'net_port', description: '检查某个主机/端口是否可达（TCP 连接测试）', parameters: { type: 'object', properties: { host: { type: 'string', description: '主机，如 1.1.1.1' }, port: { type: 'number', description: '端口' } }, required: ['host', 'port'] } }
    ];
  },
  async execute(name, params) {
    try {
      if (name === 'net_ip') {
        const includeGeo = params.geo !== false;
        let ip, geo = '';
        try {
          const body = await httpGet('http://ip-api.com/json?fields=status,query,country,regionName,city,isp,org,as,timezone', 8000);
          const p = JSON.parse(body);
          if (p.status === 'success') {
            ip = p.query;
            geo = `归属地:${p.country} ${p.regionName} ${p.city} | ISP:${p.isp} | AS:${p.as} | 时区:${p.timezone}`;
          }
        } catch (e) {}
        if (!ip) ip = (await httpGet('https://api.ipify.org', 8000)).trim();
        return includeGeo ? `IP:${ip} | ${geo || '无归属地'}` : `IP:${ip}`;
      }
      if (name === 'net_dns') {
        const host = params.host;
        const a = await dns.promises.resolve4(host).catch(() => []);
        const mx = await dns.promises.resolveMx(host).catch(() => []);
        const list = [];
        (a || []).forEach(x => list.push('A ' + x));
        (mx || []).sort((x, y) => x.priority - y.priority).slice(0, 5).forEach(x => list.push('MX ' + x.exchange));
        if (!list.length) return '该域名没有 A/MX 记录（可能解析失败）';
        return host + ' 解析:\n' + list.join('\n');
      }
      if (name === 'net_port') {
        const host = params.host, port = Number(params.port);
        if (!host || !port) return '需要 host 和 port';
        return await new Promise(res => {
          const s = net.connect(port, host);
          const done = ok => { try { s.destroy(); } catch (e) {} res(ok ? `✅ ${host}:${port} 可连接` : `❌ ${host}:${port} 不可达`); };
          s.setTimeout(5000, () => done(false));
          s.on('connect', () => done(true));
          s.on('error', () => done(false));
        });
      }
      return null;
    } catch (e) {
      return 'netdoctor执行错误:' + (e && e.message ? e.message : String(e));
    }
  }
};
