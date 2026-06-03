# OmniSkill

OmniSkill 是一套面向 OmniOperator 向量化执行引擎的 AI 辅助技能集合，涵盖表达式/算子生态分析与表达式开发流程。

## 技能列表

### omnioperator-expression-analysis

表达式生态分析技能 —— 系统扫描 OmniOperator / Velox / Spark SQL 三方表达式支持现状，生成覆盖度 Gap 分析报告。

- OmniOperator 向量化框架已注册哪些表达式？各支持哪些类型？
- Velox 覆盖了哪些 Spark SQL 表达式？
- Omni 与 Velox / Spark SQL 标准之间的差距是什么？优先级如何？

详见 [omnioperator-expression-analysis/README.md](omnioperator-expression-analysis/README.md)

### omnioperator-status-analysis

算子生态分析技能 —— 全链路扫描 Spark / Gluten / OmniOperator / Velox 物理算子支持现状，生成覆盖度 Gap 分析报告。

- 当前 Omni 支持哪些 Spark 物理算子？
- 哪些算子只在 Gluten 侧声明了支持但执行侧还不完整？
- fallback 主要来自哪些算子或类型限制？
- 与 Velox 相比，Omni 缺哪些算子能力？

详见 [omnioperator-status-analysis/README.md](omnioperator-status-analysis/README.md)

### omnioperator-expression-dev

表达式开发技能 —— 指导在 OmniOperator 向量化执行引擎中开发新表达式/函数的完整流程。

- 需求分析 → 研究 Velox 参考实现 → 研究 OmniOperator 现有模式
- 编写设计文档 → 用户审批 → 实现函数 → 注册函数 → 编写单元测试
- 提供头文件、实现文件、测试文件模板

详见 [omnioperator-expression-dev/README.md](omnioperator-expression-dev/README.md)

## 目录结构

```
OmniSkill/
├── README.md
├── omnioperator-expression-analysis/   ← 表达式生态分析技能
│   ├── SKILL.md
│   └── README.md
├── omnioperator-expression-dev/        ← 表达式开发技能
│   ├── SKILL.md
│   ├── README.md
│   └── references/                     ← 模板与参考文档
├── omnioperator-status-analysis/       ← 算子生态分析技能
│   ├── SKILL.md
│   ├── README.md
│   └── references/                     ← 模板与参考文档
└── .gitignore
```