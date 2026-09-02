# AI 自动化渗透架构 Cairn 满分登顶 TsecBench Cybench —— 众人向左，我偏向右

原创 l3yx 

淚笑的赛博日记-起零衍迹实验室

 *2026年8月29日 20:06* *广东* 听全文

## **前言**

TsecBench 是腾讯安全云鼎实验室推出的智能攻防 AI 评测平台。Cairn_Y 是基于 Cairn （https://github.com/oritera/Cairn）架构改进的 AI 自动化渗透引擎，以满分登顶 Cybench 评测基准第一，同时以绝对高分优势登顶 Tsecbench v1 第一，XBOW Validation Benchmarks 第一。

Cairn_Y 没有针对靶场赛题定向优化提示词，更不会内嵌答案或携带跨轮记忆，只在 Harness 工程上做迭代改进。TsecBench 平台的榜单也公开全部 llm 记录，提供作弊举报功能，所有成绩接受大家严格监督检查。

![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/Ukaia78jfYMKobvia0uIEQVxia17dfqY2lciaSzCLgUjibt6J3ibZ6RvYuXo9FqSW81cnXmuuT1bDVPehJKpurwrNnqSojiaDjwOu64k27oDhoqkicw/640?wx_fmt=png&from=appmsg&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=0)



![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/Ukaia78jfYMIaSMZczvrLjsM9bPjRlTfnwpItJHs8JkadDQianFNO6TufxgD03QfkJVqBa8lGXkbrzIgTa0s3VIsH17EawVmUA26JTibJ9KeRI/640?wx_fmt=png&from=appmsg&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=1)



![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/Ukaia78jfYMKRwWUvg57LCFr0L1QMZnnYDnXGcbcwh8FcYrQG3ASbby7sKJekrjn6WJCzLIHqcq7G4ns1vnrkySKoTfTiaCz6cxN6IGoOsX4s/640?wx_fmt=png&from=appmsg&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=2)

## **从 Cairn 架构说起**

Cairn 是我参加腾讯第二届 TCH 智能渗透挑战赛的作品，详细信息可以参考[《国内最强 AI 渗透测试 Agent —— TCH·腾讯云黑客松第二届智能渗透挑战赛 唯一 AK 战队复盘》](https://mp.weixin.qq.com/s?__biz=Mzk2NDgwMTY5Nw==&mid=2247484051&idx=1&sn=31b30c8f8b381dd434fd98f99330e848&scene=21#wechat_redirect) 和 [《无径之径：Cairn AI 从渗透测试到通用问题的求解》](https://mp.weixin.qq.com/s?__biz=Mzk2NDgwMTY5Nw==&mid=2247484103&idx=1&sn=5d51501626ad6cdf9e3fedd421a74892&scene=21#wechat_redirect) 。

我当时在 Cairn 中首先创新地提出了包括**意图工程**、**黑板架构**、**间接协调**、**Fact-Intent** DAG 图、**状态空间搜索**等概念，并且贯彻 **Less Is More**、**极简**、减少约束释放模型能力等理念，还有 0 Skill、0 RAG、0 MCP、反对以具体任务角色为主的多 Agent 架构、不预设任何渗透攻击流程等一系列在当时非常反直觉甚至可以说离经叛道的设计。

虽然 Cairn 在当时与大众的 Agent 设计走了完全相反的道路，但其比赛成绩反而可以说明架构领先性，此外腾讯也基于 TSecBench 统一评测体系，联合各大高校对国内外代表性智能渗透 Agent 开展了公开测试。测试结果中，Cairn 依然是任务成功率最高的 Harness 工程之一：

![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/Ukaia78jfYMJAhBYjRk8gRnLEugiaIcCQznmhpWMyWNsYULPkchpHmhMLwHVS6o3mfN1AGlnfXddtEiaHSnM6ygFO4tJ96NCJWpZRwXXE7oZys/640?wx_fmt=png&from=appmsg&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=3)



另外在公开评测榜单和其他相关比赛中，我们也观察到靠前的智能体提示词中高频出现了 **Fact**、**Intent** 等关键术语、Cairn DAG 的数据结构或其他类似的 Cairn 的黑板。社区也出现了大量基于 Cairn 二开，或者基于 Cairn 理念设计的 AI 渗透智能体。

Cairn 项目本身目前没有较快的迭代是因为我对 Cairn 的定位不是一个功能完备的渗透测试项目，而是当时我对极简主义，黑板架构，Fact-Intent 图的理解与运用的一次工程实践。

## **Cairn_Y 方案**

虽然最近没有正式更新过代码或写过技术文章，但我在原本的 Cairn 架构之上还是做了很多工作才有了现在的 Cairn_Y，Cairn_Y 的设计依旧非常克制，依旧贯彻 **Less Is More**。

这几个月里我也经常关注开源社区中智能渗透 Agent 的发展，看了很多迭代改进的方案，比如 “跨题学习”、“幻觉门控”、“数百工具集成”，也看了很多完全不一样的渗透 Agent 的设计，比如 “多类专业 Subagent”，“数十内置 Security Skill”。

Cairn_Y 的改进方向与这些方案完全没有关系。

## **FGS 图：外化的记忆与世界状态描述**

与 Cairn 一样，依旧把渗透测试这类任务建模为**面向目标的状态空间搜索**，把搜索过程的全部状态外化成一张只追加的图。Cairn_Y 中图的结构有了变化与正式名称： **FGS 图** （Fact-Goal-Step Grpah）。

- **Fact** 描述当前已经确认的事实，即当前的世界状态。
- **Goal** 描述项目的完成条件，即搜索的终止条件。也存在可以动态增删的 **Sub Goal**。
- **Step** 描述下一步要做什么，如何从已有事实产生新的事实，是推动世界状态演化的因果过程。

## **Decide & Execute：极简的运转方式**

系统的运行只有 **Decide** 和 **Execute** 两类活动，二者是独立的运行过程，而非固定角色的 SubAgent，也可以理解为同一个运行器被注入了不同的提示词与工具。

- **Decide** 只拥有查看和操作 **FGS 图** 的 Tool，在一个任务开始时或者图上信息有增加或者变化时被触发，串行执行，作用就是分析当前任务的状态，对图上 **Step** 进行评估，进行 Step 的添加、废弃或者优先级调整。也可以提出和删除  **Sub Goal**，帮助整体任务可以阶段性的平稳推进。每次触发都从干净上下文重新起一次运行，不携带记忆。
- **Execute** 拥有查看 **FGS 图** 的权限，和所有可以对 “世界状态” 产生变化的 Tool，比如 `read`、`bash`、`edit`、`write` 等，还有最重要的提交 **Fact** 的 Tool `submit_fact`。

两类活动的本质其实都是注入了不同 Tool 和 Prompt 的 Agent Loop，但都没有独立的跨会话记忆机制，**FGS图** 就是他们共同的、外化的记忆。

## **Finding：搜索过程的结构化产物**

**Finding** 是 Cairn_Y 引入的一个很重要的概念。因为我觉得无论是渗透测试、代码审计，还是 CTF，都是一种搜索过程，CTF 这种任务很单纯，搜索的产物和最终的 **Goal** 是一样的，即 Flag，但渗透测试和代码审计所需要的产物更多是搜索过程中发现的漏洞，而不是像 “完成所有功能点测试与漏洞挖掘” 这种最终 **Goal**。

所以对于这种关注搜索过程中的产物的任务，我引入了 **Finding** 的概念，理论也很完备 —— 搜索过程当然既有终点也有搜索过程发现的东西。**Finding** 也是可以动态定义的，对于漏洞挖掘类的任务，**Finding** 定义自然就是安全漏洞。

## **工程选型**

- Cairn_Y 的 Agent Loop 已不再使用 Claude Code 或者 CodeX 这类完善的 Agent 或者 SDK，目前是基于 PI 的库，也仅用到了 PI 的 Agent Loop 功能。值得一提的是我不是因为觉得 PI 更强大，反而是因为 PI 更简陋，更容易完全被我控制。在后续的的迭代改进中，我应该会选择完全自写 Agent Loop。
- 技术栈我已经从 Cairn 的 Python 切换到了 Node，因为我发现不管是 Claude Code 还是 Codex 或者 PI，都是 Node 的生态。但后续我更倾向用 Go 或 Rust 写更轻量的版本。
- 整个 Cairn_Y 的内置提示词非常短，而且不和安全类任务耦合，它依然是和 Cairn 一样，是一种通用的任务解决引擎。

## **最后**

这篇文章是我手写的，没用 AI 生成和辅助，在当前这个浮躁的时代太多 AI 生成的通篇废话文学了，我希望输出的是一些个人真实实践经验和见解，也希望公众号推送里少出现一些纯 AI 文。

Tsecbench v1 共 63 题，74 个 flag，在半年前的 TCH 比赛中想要全解需要 7000 元左右成本，目前的 Cairn_Y 最低只需要不到 50 元，而且依旧有较大优化空间。我最近也在一直研究本地模型的方案。以后一个网站的渗透测试成本可能也就几毛钱。不久的未来，网络安全攻防的成本结构和规则将被彻底颠覆。

在一段时间的开发和实践后，我感受是大多数看起来很厉害很有道理的 Agent 架构设计其实并没有用，甚至会起反效果，真正厉害的是模型，不是外层臃肿的 Agent 架构，也不是那一堆冗余的 Skill。