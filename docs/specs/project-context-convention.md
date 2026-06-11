# Project Context Convention

## 目标

给 Rabbit 定义一套统一、低摩擦的项目上下文文件规范，用于描述：

- 授权状态
- 目标暴露形态
- 允许/禁止动作
- 输出要求
- 项目级目标与备注

这套规范的核心目标不是增加填写成本，而是让系统默认继承一份稳定的“授权上下文基线”，项目只有在确实有例外时才补一层薄覆盖。

---

## 文件命名

### 1. 全局默认文件

固定文件名：

`/Users/rabbit/Desktop/Rabbit/.rabbit/context/default.project-context.yaml`

用途：

- 作为所有项目的默认上下文基线
- 尽量长期稳定
- 大多数情况下只维护这一份

### 2. 项目级覆盖文件

目录：

`/Users/rabbit/Desktop/Rabbit/.rabbit/context/projects/`

文件名规则：

`{project_id}.project-context.yaml`

示例：

- `proj_015.project-context.yaml`
- `proj_023.project-context.yaml`

用途：

- 只描述某个项目相对于全局默认的差异
- 默认只填项目名称、目标摘要、目标和备注
- 只有出现特殊授权边界时，才写 override

---

## 解析顺序

未来如果系统接入自动注入，建议按下面顺序合并：

1. 内置系统默认值
2. `default.project-context.yaml`
3. `projects/{project_id}.project-context.yaml`
4. 当前项目表单中的显式字段

后者覆盖前者。

---

## 填写规范

### A. 默认只填项目段

对绝大多数项目，只需要写：

```yaml
project:
  project_id: proj_023
  project_name: 目标名称
  target_summary: 公网可达但属于授权测试环境
  goal: 验证攻击路径并形成报告
  notes: 特殊说明
```

不要重复填写授权、scope、output。

### B. 只有例外才写 override

示例：

```yaml
override:
  authorization:
    engagement_type: customer-authorized-assessment
  scope:
    allowed_actions:
      - analyze
      - verify
      - summarize
      - report
      - retest
```

### C. 数组字段语义

建议统一为“显式覆盖”，不要做隐式追加。

也就是说：

- 如果 override 里写了 `allowed_actions`
- 就用 override 里的完整数组替代默认值

这样最简单，也最不容易混乱。

---

## 为什么这样设计

这套设计满足三个要求：

### 1. 无感

大部分时候只维护一个全局文件。

### 2. 统一

所有项目都用同一个目录、同一个文件名模式、同一个字段结构。

### 3. 易于后续接系统

以后无论是在：

- 新建项目流程
- Worker prompt 注入
- 报告生成
- 项目详情页展示

都可以直接按这个路径和格式读取，不需要再重新设计数据结构。

---

## 推荐的人工工作流

### 日常默认

只维护：

`default.project-context.yaml`

### 某个项目需要补充时

复制：

`/Users/rabbit/Desktop/Rabbit/.rabbit/context/projects/_template.project-context.yaml`

改名为：

`{project_id}.project-context.yaml`

然后只填写：

- `project_id`
- `target_summary`
- `goal`
- `notes`

---

## 当前建议

当前实现已经接入两层自动行为：

1. Dispatcher 在生成 bootstrap / reason / explore 提示词时，会自动注入：
   - `default.project-context.yaml`
   - `projects/{project_id}.project-context.yaml`
2. 新建项目时，系统会自动生成一个薄覆盖文件：
   - `projects/{project_id}.project-context.yaml`

自动生成文件默认只写：

- `project_id`
- `target_summary`（初始使用项目 origin）
- `goal`
- `notes`

默认不写 `project_name`，避免项目重命名后产生陈旧上下文。

这样大多数项目创建后就已经具备统一授权语境，你只需要在少数项目上补例外项。
