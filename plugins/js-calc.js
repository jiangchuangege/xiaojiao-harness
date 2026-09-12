// 小焦 · JS 插件示例：计算器
module.exports = {
  getToolDescriptions() {
    return [{
      name: "js_calc",
      description: "算数学表达式(如 (3+4)*2)。什么时候用：用户要算数且不需要解释过程；输入纯数字/运算符表达式；输出 计算结果",
      parameters: { type: "object", properties: { expr: { type: "string", description: "数学表达式，如 (3+4)*2" } }, required: ["expr"] }
    }];
  },
  execute(name, params) {
    if (name === "js_calc") {
      const expr = String(params.expr || "");
      // 白名单校验：只允许数字 + 基本运算符
      if (!/^[\d\s+\-*/().%^]+$/.test(expr)) return "只支持纯数字运算表达式";
      // 把 ^ 换成 ** 再求值
      let v;
      try { v = Function('"use strict";return (' + expr.replace(/\^/g, "**") + ')')(); }
      catch (e) { return "表达式错误: " + e.message; }
      return String(v);
    }
    return null;
  }
};
