---
name: mobiagent-task-spec
description: Use this skill whenever a task involves MobiAgent or steward mobile execution. It defines how to write high-precision mobile task descriptions, including goal + step-level click/swipe/input instructions, explicit completion criteria, and evidence-driven rewrites based on final screenshot + status/reasoning + VLM extraction evidence. Trigger for requests about mobile automation success rate, steward task wording, evidence-based correction, or improving phone-side accuracy.
---

# MobiAgent Task Spec

用于把“模糊手机任务”改写成 MobiAgent 可执行、可验证的描述，并指导 steward 正确消费单次执行结果。

## 1) 能力边界（必须先遵守）

- MobiAgent 本质是基础动作组合执行器：`点击`、`滑动`、`输入`（以及导航过程中的等价基础操作）。
- 不能把它当作“高级意图 API”来用。避免要求“总结”“截图”“导出报告”“直接读取某字段”这类一步到位动作。
- 任务描述尽可能落到页面元素与动作序列上，否则会漂移或死循环。

## 2) 任务描述原则

- 目标单一：每次下发手机执行的任务只涉及1个APP,只针对1个完整任务。
- 步骤可执行：每一步都写“看到什么 -> 做什么”。
- 完成可判定：必须有可观察的成功标准，不用“应该/大概/尽量”。
- 约束可控：写清副作用约束（例如“不要发送消息，只停留在确认页”）。

## 3) 标准任务模板（自然语言版，给 steward 直接用）

`task_desc` 只写一段自然语言，不要使用分段标题，也不要分点罗列。  
写法要求：把核心目标、关键步骤、失败分支、成功判定、禁止项合并为连续描述。  
重试时也一样：`task_desc` 里只保留“修正后的任务描述正文”，不要包含“失败原因/修正点/改写说明”。

可直接套用下面句式并替换占位符：

```text
请在<App>中完成以下任务：先确保当前位于<App>首页，如果不在则回到首页；然后定位<锚点A>并点击进入<页面B>，如果没看到<锚点A>就先滑动一次再重试，连续两次仍找不到就回退到上一级页面后重新从首页开始；进入<页面B>后定位<锚点C>，必要时在搜索框输入<关键词>并进入目标页面，最终停留在包含<锚点D>的页面。任务完成标准是同时满足<页面级锚点>和<目标对象锚点>可见，并且屏幕中能看到<证据关键词>；执行过程中不要进行<禁止动作1>和<禁止动作2>等与目标无关操作。
```

任务描述 `task_desc` 示例：
- 简易版：打开爱奇艺浏览最近的电影预告。
- 详细版：使用爱奇艺应用查看新片预告，具体操作步骤为：1. 进入爱奇艺首页后，连续向上滑动屏幕浏览页面内容；2. 在页面下方找到“新片即将上线”的相关板块；3. 点击该板块中的“更多新片预告”按钮，即可进入详情页面查看即将上线的新片信息。

## 4) 好坏对比（必须避免坏例子）

坏例子（不可执行）：
- “帮我把微信最近消息整理一下并截图。”
- “去支付宝看下账单然后告诉我重点。”

好例子（可执行）：
- “请在微信中找到‘项目A群’聊天页并停留：先进入微信首页，点击顶部搜索输入‘项目A群’后进入聊天页；完成标准是页面顶部显示‘项目A群’且聊天区可见最近消息文本，不要发送任何消息。”
- “请在支付宝中进入本月账单列表页并停留：先打开支付宝并进入‘账单’，再筛选‘本月’，连续失败则返回账单首页重试；完成标准是页面同时出现‘账单’和‘本月’文本且可见金额（如¥），不要进行支付或转账操作。”

## 5) 重试改写流程（基于已有证据）

当执行失败后，必须读取并利用以下证据再改写任务：
- 最后一张截图（ImageBlock / `final_image_url`）
- `last_reasoning`
- `status_hint`
- `vlm_summary_screen_state` / `vlm_summary_last_steps`
- `vlm_summary_relevant_information` / `vlm_summary_extracted_text`

注意：不要依赖已废弃的 `ocr_text` / `screenshot_path` 字段。

按下面顺序改写：

1. 先定位失败类型  
- 锚点不明确：VLM提取文本里没有可匹配文本  
- 页面走偏：截图显示进入了错误页面  
- 步骤过粗：一个步骤含多个动作决策  
- 约束缺失：执行了不希望的副作用操作

2. 再做定向修正  
- 把模糊锚点改为唯一锚点（文本、页签名、按钮名）  
- 把大步骤拆细（每步只做一个主要动作）  
- 加入回退路径（找不到锚点时先回退/回首页）  
- 加入禁止项（如“不要发送，不要提交，不要删除”）

3. 输出最终 `task_desc`（仅输出修正后的任务描述）

```text
<按“自然语言模板”重写后的完整任务描述。不要包含“上次失败原因”“本次修正点”“改写说明”等解释性文字。>
```

## 6) steward 专用执行建议

- 执行手机采集时，只允许调用 `call_mobi_collect_with_report`。
- 不要调用或猜测其他旧工具名：如`call_mobi_collect_verified`、`call_mobi_collect_with_retry_report`。
- `call_mobi_collect_with_report` 的返回结果已经包含 VLM 页面摘要、目标相关信息、截图提取文本和最后截图；应直接消费这些结果，不要声称“工具没有返回这些内容”。
- 如果结果不足以回答用户问题，应基于当前证据明确说明缺口，而不是改用未注册工具名盲试。

## 7) 最小检查清单（发送给 MobiAgent 前）

- 是否只有1个APP的1个完整任务？
- 是否每一步都有可见锚点？
- 是否定义了“找不到锚点怎么办”？
- 是否定义了明确成功判定？
- 是否写了禁止项与副作用约束？
- 若是重试，是否引用了上次证据来改写？
