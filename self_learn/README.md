# 小焦 · 持续学习引擎（小脑跟着大脑学）

> **别人靠算力，小脑靠文本。** 小脑不强在算力，强在一本「跟着大脑学来的、越来越厚的**功能用法**」文本文件 + 检索。

## 它学什么（不是什么都学）
小脑只学**功能能力点**——即"**用户要干什么 → 大脑用什么工具、怎么解决**"这套**可检索复用的功能套路**。比如：
- 用户要"建个文件夹写文件" → 大脑调用 `run_command` + `write_file` 的流程。
- 用户要"查IP" → 大脑调用 `get_ip`（netdoctor 插件）。

这些**怎么用工具**的功能模式，被记进一个文本库，小脑检索命中即可复用 → 小脑越来越强。

## 数据流

```
① 日常使用   用户 → 大脑推理 → 用工具 → 回复
② 记录层     log 每次交互(用户/回答/工具轨迹) → logs/chat_history.jsonl
③ 反馈层     👍/👎/评分/用户更正 → logs/feedback.jsonl
④ 数据积累   只把「高质量/被点赞/被更正」的交互 → self_learn/little_brain_knowledge.txt(越长越强)
             （同时同步进小脑检索池 training_data_pool_clean.txt）
⑤ 训练层(可选) 数据够多后可重训小模型
⑥ 部署       验证 → 切换新模型；变差自动回退
```

## 怎么用（`cd self_learn`）

```powershell
# 1) 大脑每答完一次，记录交互(第3个参数=工具轨迹, 可选)
python learn.py log "在桌面建tests1写上index.html" "已完成" '[{"tool":"run_command"},{"tool":"write_file"}]'

# 2) 用户点反馈/更正
python learn.py feedback <log_id> good          # 点赞
python learn.py feedback <log_id> 5星
python learn.py feedback <log_id> bad "更正的答案"   # 踩+更正(更正过的必定学)

# 3) 隔段时间，把高质量交互灌进小脑知识库(越长越强)+同步检索池
python learn.py build

# 4) (可选)数据够多后重训小模型
python learn.py train
```

## 挂进小焦（不强制，可选）
现在**不改你现有文件**。想让"每次对话自动记录+打钩"，可以在小焦的后端做两个轻量 hook：
- 答完 → `subprocess.run(["python","self_learn/learn.py","log", user, answer, tool_trace])`
- 用户在网页点 👍/👎 → 调 `feedback`

（这些 hook 属于可选的接入，需要你确认后再加；当前这版独立可用、不动现有代码。）

## 目录
```
self_learn/
└── learn.py            # 持续学习引擎(log/feedback/build/train)
└── little_brain_knowledge.txt   # ★ 小脑知识库：越长越强(由 build 生成)
```
日志在项目根 `logs/chat_history.jsonl`（记录层）+ `logs/feedback.jsonl`（反馈层）。
