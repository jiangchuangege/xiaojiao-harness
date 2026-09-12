---
name: Archify 画图工作流
applies_to: archify_* 工具
---

# Archify 画图工作流（必须严格按顺序执行）

当用户要求**画图**（架构图 / 流程图 / 时序图 / 数据流图 / 状态图 / 生命周期图）时：

1. 调 `archify_read_skill`，读完 SKILL.md（**同一会话只读一次**）。
2. 调 `archify_guide(scenario, lang="zh")`，拿推荐类型与官方提示词。
3. 按推荐类型调 `archify_read_schema(type)`，读 schema + common + README。
4. 调 `archify_read_example(type)`，照真实示例生成完整 JSON。
5. 调 `archify_validate(type, spec_json, quality="showcase")`；
   若 FAIL，按 issues 修正 JSON 再校验，**直到 PASS**（最多修 3 轮）。
6. 调 `archify_deliver(type, spec_json, output_name, quality="showcase")`。
7. 调 `archify_visual_check(html_path)` 确认渲染质量。
8. 把 HTML 的**绝对路径**告诉用户。

## 硬性规则

1. 禁止跳过第 1、2 步。
2. 禁止简化 JSON；`quality_profile` 必须是 `"showcase"`。
3. 校验 FAIL **禁止交付**。
4. 批量任务用 `archify_batch`；想看统计调 `archify_metrics`。
5. 环境不对（缺依赖 / Node 版本）先调 `archify_doctor` 查清再继续。

> 说明：这份工作流以前写在 `xiaojiao_control.json` 的 `role` 里，属于**架构错误**
> （人设里塞工具规则：改人设丢规则、加插件要手改人设、role 越写越长）。
> 现在按分层约定搬到这里：`role` 只留人设，工具规则由代码统一拼（`_TOOL_RULES`），
> 插件清单由 `_plugin_list()` 从 PLUGINS **动态生成**。
